from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import require_admin
from app.integrations.alpaca import (
    AlpacaTradingConfigurationError,
    AlpacaTradingError,
)
from app.schemas.options import (
    OptionContractSelectionCreate,
    OptionContractSelectionRead,
)
from app.services.option_contracts import (
    OptionContractNotFoundError,
    OptionContractSelectionError,
    select_option_contract,
)

router = APIRouter(
    prefix="/options",
    tags=["options"],
    dependencies=[Depends(require_admin)],
)


@router.post(
    "/select-contract",
    response_model=OptionContractSelectionRead,
    status_code=status.HTTP_200_OK,
)
def select_option_contract_route(
    payload: OptionContractSelectionCreate,
) -> OptionContractSelectionRead:
    try:
        return select_option_contract(payload)
    except OptionContractNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except OptionContractSelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except AlpacaTradingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except AlpacaTradingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail,
        ) from exc
