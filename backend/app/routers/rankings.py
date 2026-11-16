import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.http_errors import to_http_exception
from app.core.security import current_user_id
from app.repositories import recommendations_repository
from app.schemas import FactSetImportResponse, RankingEntry
from app.services import factset_service, ranking_service
from app.services.errors import ServiceError, ValidationError

router = APIRouter(prefix="/api/v1/rankings", tags=["rankings"])
MAX_FACTSET_FILE_BYTES = 10 * 1024 * 1024
MAX_FACTSET_FILES = 20


def _responses(rows) -> list[RankingEntry]:
    return [RankingEntry(rank=index, **row._asdict()) for index, row in enumerate(rows, start=1)]


@router.get("", response_model=list[RankingEntry])
def list_rankings(
    request: Request,
    limit: int = Query(default=20, ge=1, le=ranking_service.MAX_LIMIT),
    sector: str | None = Query(default=None),
    methodology_version: str | None = Query(default=None),
) -> list[RankingEntry]:
    try:
        with request.app.state.db.connection() as connection:
            rows = ranking_service.get_rankings(
                connection, limit=limit, sector=sector, methodology_version=methodology_version
            )
    except ServiceError as exc:
        raise to_http_exception(exc)

    return _responses(rows)


@router.post("/factset", response_model=FactSetImportResponse)
async def import_factset_summaries(
    files: list[UploadFile] = File(...),
    _user_id: int = Depends(current_user_id),
) -> FactSetImportResponse:
    if not files or len(files) > MAX_FACTSET_FILES:
        raise to_http_exception(ValidationError(f"Upload between 1 and {MAX_FACTSET_FILES} CSV files."))

    with tempfile.TemporaryDirectory(prefix="signalist-factset-") as temporary:
        source = Path(temporary)
        for index, upload in enumerate(files):
            if Path(upload.filename or "").suffix.lower() != ".csv":
                raise to_http_exception(ValidationError("FactSet Summary uploads must be CSV files."))
            content = await upload.read(MAX_FACTSET_FILE_BYTES + 1)
            if len(content) > MAX_FACTSET_FILE_BYTES:
                raise to_http_exception(ValidationError("Each FactSet Summary must be 10 MB or smaller."))
            (source / f"summary-{index + 1}.csv").write_bytes(content)

        try:
            result = await run_in_threadpool(factset_service.import_summaries, source)
        except ServiceError as exc:
            raise to_http_exception(exc)

    rankings = recommendations_repository.top_ranked(limit=20)
    return FactSetImportResponse(
        success=result.success,
        imported_count=result.imported_count,
        error_count=result.error_count,
        warning_count=result.warning_count,
        validation_messages=factset_service.validation_messages(result),
        rankings=_responses(rankings) if result.success else [],
    )
