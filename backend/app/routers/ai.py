import os

from fastapi import APIRouter, Depends, Request, status

from app.core.http_errors import to_http_exception
from app.core.security import current_user_id
from app.schemas import AIConfigResponse, AIMessageResponse, AIReportResponse, CreateAIMessageRequest, CreateAIReportRequest
from app.services.errors import ServiceError
from app.ai import repository
from app.ai import providers

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


@router.get("/config", response_model=AIConfigResponse)
def get_config(user_id: int = Depends(current_user_id)) -> AIConfigResponse:
    del user_id
    selected_provider = providers.provider()
    return AIConfigResponse(
        provider=selected_provider,
        data_policy=providers.data_policy(),
        model=providers.model_name(),
        embedding_model=providers.embedding_model_name(),
        embedding_dimensions=providers.embedding_dimensions(),
        api_key_configured=selected_provider == "ollama" or bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")),
    )


def _report_response(report: dict) -> AIReportResponse:
    return AIReportResponse(**report)


@router.post("/reports", response_model=AIReportResponse, status_code=status.HTTP_202_ACCEPTED)
def create_report(request: Request, payload: CreateAIReportRequest, user_id: int = Depends(current_user_id)) -> AIReportResponse:
    try:
        with request.app.state.db.connection() as connection:
            return _report_response(repository.create_report(connection, user_id, payload.kind, payload.ticker, payload.backtest_id))
    except ServiceError as exc:
        raise to_http_exception(exc)


@router.get("/reports", response_model=list[AIReportResponse])
def list_reports(request: Request, user_id: int = Depends(current_user_id)) -> list[AIReportResponse]:
    with request.app.state.db.connection() as connection:
        return [_report_response(row) for row in repository.list_reports(connection, user_id)]


@router.get("/reports/{report_id}", response_model=AIReportResponse)
def get_report(request: Request, report_id: int, user_id: int = Depends(current_user_id)) -> AIReportResponse:
    try:
        with request.app.state.db.connection() as connection:
            return _report_response(repository.get_report(connection, user_id, report_id))
    except ServiceError as exc:
        raise to_http_exception(exc)


@router.post("/reports/{report_id}/messages", response_model=AIMessageResponse, status_code=status.HTTP_202_ACCEPTED)
def create_message(request: Request, report_id: int, payload: CreateAIMessageRequest, user_id: int = Depends(current_user_id)) -> AIMessageResponse:
    try:
        with request.app.state.db.connection() as connection:
            row = repository.enqueue_message(connection, user_id, report_id, payload.content)
            return AIMessageResponse(**row)
    except ServiceError as exc:
        raise to_http_exception(exc)


@router.get("/reports/{report_id}/messages", response_model=list[AIMessageResponse])
def list_messages(request: Request, report_id: int, user_id: int = Depends(current_user_id)) -> list[AIMessageResponse]:
    try:
        with request.app.state.db.connection() as connection:
            return [AIMessageResponse(**row) for row in repository.list_messages(connection, user_id, report_id)]
    except ServiceError as exc:
        raise to_http_exception(exc)
