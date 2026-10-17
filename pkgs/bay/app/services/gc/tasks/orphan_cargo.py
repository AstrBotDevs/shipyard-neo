"""OrphanCargoGC - Clean up orphan managed cargos."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.managers.cargo import CargoManager
from app.models.cargo import Cargo
from app.models.sandbox import Sandbox
from app.services.gc.base import GCResult, GCTask

if TYPE_CHECKING:
    from app.drivers.base import Driver

logger = structlog.get_logger()

_TERMINAL_RUNTIME_STATES = frozenset({"dead", "exited", "failed", "succeeded"})


class OrphanCargoGC(GCTask):
    """GC task for cleaning up orphan managed cargos.

    Trigger condition:
        cargo.managed = True AND (
            cargo.managed_by_sandbox_id IS NULL OR
            sandbox.deleted_at IS NOT NULL
        )

    Action:
        Delete cargo via CargoManager.delete_internal_by_id()
    """

    def __init__(
        self,
        driver: "Driver",
        db_session: AsyncSession,
    ) -> None:
        self._driver = driver
        self._db = db_session
        self._log = logger.bind(gc_task="orphan_cargo")
        self._cargo_mgr = CargoManager(driver, db_session)

    @property
    def name(self) -> str:
        return "orphan_cargo"

    async def run(self) -> GCResult:
        """Execute orphan workspace cleanup."""
        result = GCResult(task_name=self.name)

        # Find orphan managed cargos
        # Case 1: managed_by_sandbox_id is NULL
        # Case 2: referenced sandbox is soft-deleted
        orphans = await self._find_orphans()

        self._log.info(
            "gc.orphan_cargo.found",
            count=len(orphans),
        )

        for cargo_id in orphans:
            try:
                if await self._has_runtime_references(cargo_id):
                    result.skipped_count += 1
                    self._log.info(
                        "gc.orphan_cargo.skip.runtime_in_use",
                        cargo_id=cargo_id,
                    )
                    continue

                await self._cargo_mgr.delete_internal_by_id(cargo_id)
                result.cleaned_count += 1
                self._log.info(
                    "gc.orphan_cargo.deleted",
                    cargo_id=cargo_id,
                )
            except Exception as e:
                self._log.exception(
                    "gc.orphan_cargo.item_error",
                    cargo_id=cargo_id,
                    error=str(e),
                )
                result.add_error(f"cargo {cargo_id}: {e}")

        return result

    async def _has_runtime_references(self, cargo_id: str) -> bool:
        """Check whether any active runtime instance still references the cargo.

        Stopped containers and completed pods retain labels until they are removed, but no
        longer use the cargo. Remove those terminal runtime objects, then query again before
        allowing cargo deletion. Unknown or transitional states remain conservative.
        """
        labels = {
            "bay.cargo_id": cargo_id,
            "bay.managed": "true",
        }
        instances = await self._driver.list_runtime_instances(labels=labels)
        if any(
            instance.state.lower() not in _TERMINAL_RUNTIME_STATES
            for instance in instances
        ):
            return True

        for instance in instances:
            self._log.info(
                "gc.orphan_cargo.remove_terminal_runtime",
                cargo_id=cargo_id,
                runtime_id=instance.id,
                runtime_state=instance.state,
            )
            await self._driver.destroy_runtime_instance(instance.id)

        if not instances:
            return False

        remaining = await self._driver.list_runtime_instances(
            labels={
                "bay.cargo_id": cargo_id,
                "bay.managed": "true",
            }
        )
        return bool(remaining)

    async def _find_orphans(self) -> list[str]:
        """Find orphan managed cargo IDs."""
        orphan_ids: list[str] = []

        # Case 1: managed=True but managed_by_sandbox_id is NULL
        query1 = select(Cargo.id).where(
            Cargo.managed == True,  # noqa: E712
            Cargo.managed_by_sandbox_id.is_(None),
        )
        result1 = await self._db.execute(query1)
        for (cargo_id,) in result1:
            orphan_ids.append(cargo_id)

        # Case 2: managed=True and referenced sandbox is soft-deleted
        # Use LEFT OUTER JOIN to find cargos where sandbox.deleted_at IS NOT NULL
        SandboxAlias = aliased(Sandbox)
        query2 = (
            select(Cargo.id)
            .outerjoin(
                SandboxAlias,
                Cargo.managed_by_sandbox_id == SandboxAlias.id,
            )
            .where(
                Cargo.managed == True,  # noqa: E712
                Cargo.managed_by_sandbox_id.is_not(None),
                SandboxAlias.deleted_at.is_not(None),
            )
        )
        result2 = await self._db.execute(query2)
        for (cargo_id,) in result2:
            if cargo_id not in orphan_ids:
                orphan_ids.append(cargo_id)

        return orphan_ids
