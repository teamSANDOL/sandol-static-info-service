from fastapi import APIRouter, Query, Path, HTTPException
from typing import Union, List
from app.utils.university_structure import (
    get_tukorea_structure,
    OrganizationGroup,
    OrganizationUnit,
)

router = APIRouter(
    prefix="/organization",
    tags=["Organization"],
    responses={404: {"description": "조직을 찾을 수 없습니다."}},
)


@router.get(
    "/tree",
    response_model=OrganizationGroup,
    summary="학교 전체 조직 트리 조회",
    description="""
학교 전체 조직 구조를 트리 형태로 반환합니다.  
최상위 루트(예: 대학본부)를 기준으로 하위 조직들을 포함한 전체 트리를 제공합니다.

- UI 초기 렌더링 또는 전체 구조 시각화 시 유용
- 응답은 재귀 구조의 JSON입니다.
""",
)
async def get_tree():
    structure = get_tukorea_structure()
    return structure.root


@router.get(
    "/search/{name}",
    response_model=List[Union[OrganizationGroup, OrganizationUnit]],
    summary="조직 이름 기반 전체 탐색",
    description="""
조직명을 기준으로 학교 전체 조직 트리에서 이름이 일치하는 모든 조직을 반환합니다.
조직명은 부분 경로가 아닌 **단일 이름**만 입력하며, 이름이 동일한 여러 조직이 반환될 수 있습니다.

예시:
- `/organization/search/입학처`
- `/organization/search/컴퓨터공학부`
""",
)
async def search_by_name(
    name: str = Path(..., description="조직 이름 (예: 입학처, 컴퓨터공학부)"),
):
    structure = get_tukorea_structure()
    return structure._search_by_name(structure.root, name)


@router.get(
    "/{path:path}/children",
    response_model=List[Union[OrganizationGroup, OrganizationUnit]],
    summary="조직 경로 기반 하위 조직 조회",
    description="""
특정 경로에 위치한 조직이 `Group`일 경우, 해당 조직의 하위 조직 목록을 리스트로 반환합니다.

- 경로는 `/`로 구분된 전체 경로를 입력합니다.
- 해당 경로의 조직이 `Unit`이라면 빈 리스트를 반환합니다.

예시:
- `/organization/단과대학/SW대학/children`
- `/organization/대학본부/입학처/children`
""",
)
async def get_children(
    path: str = Path(..., description="조직 경로 (예: 단과대학/SW대학)"),
):
    structure = get_tukorea_structure()
    result = structure.get_unit(path)
    if isinstance(result, OrganizationGroup):
        return result.as_list()
    return []


@router.get(
    "/{path:path}",
    response_model=Union[OrganizationGroup, OrganizationUnit, list],
    summary="조직 경로 기반 조회",
    description="""
조직 경로(`/` 구분자 사용)를 기반으로 특정 조직 정보를 조회합니다.

- `Group`일 경우 하위 포함 구조로 반환
- `Unit`일 경우 전화번호 및 URL 포함 정보 반환
- 존재하지 않으면 404 에러 발생

예시:
- `/organization/단과대학/SW대학/컴퓨터공학부`
- `/organization/대학본부/입학처`
- `/organization/입학처`

> ⚠️ 이 라우터는 `/search`, `/tree`, `/children` 보다 하단에 선언되어야 경로 충돌을 피할 수 있습니다.
""",
)
async def get_organization(
    path: str = Path(..., description="조직 경로 (예: 단과대학/SW대학/컴퓨터공학부)"),
):
    structure = get_tukorea_structure()
    result = structure.get_unit(path)
    if result is None:
        raise HTTPException(status_code=404, detail="조직을 찾을 수 없습니다.")
    return result
