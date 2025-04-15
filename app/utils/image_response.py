import base64
import io
import zipfile
from typing import Literal

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse


async def build_image_response_from_url(
    url: str, response_type: Literal["base64", "binary"]
):
    """이미지 URL을 받아 클라이언트 요청 타입에 따라 응답을 반환합니다."""
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=10)

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="이미지 다운로드 실패")

    if response_type == "base64":
        encoded = base64.b64encode(response.content).decode("utf-8")
        return JSONResponse(content={"image_base64": encoded})

    if response_type == "binary":
        return StreamingResponse(
            iter([response.content]),
            media_type="image/jpeg",
            headers={"Content-Disposition": "inline; filename=shuttle.jpg"},
        )

    raise HTTPException(status_code=400, detail="잘못된 response_type입니다.")


async def build_multi_image_response(image_urls: list[str], response_type: str):
    if response_type == "json":
        return JSONResponse(content={"image_urls": image_urls})

    elif response_type == "base64":
        base64_list = []
        async with httpx.AsyncClient() as client:
            for url in image_urls:
                resp = await client.get(url, timeout=10)
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=502, detail=f"이미지 다운로드 실패: {url}"
                    )
                encoded = base64.b64encode(resp.content).decode("utf-8")
                base64_list.append(encoded)
        return JSONResponse(content={"image_base64_list": base64_list})

    elif response_type == "binary":
        # 이미지를 zip으로 묶어서 바이너리로 응답
        zip_buffer = io.BytesIO()
        async with httpx.AsyncClient() as client:
            with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                for idx, url in enumerate(image_urls, 1):
                    resp = await client.get(url, timeout=10)
                    if resp.status_code != 200:
                        raise HTTPException(
                            status_code=502, detail=f"이미지 다운로드 실패: {url}"
                        )
                    zip_file.writestr(f"shuttle_{idx}.jpg", resp.content)
        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=shuttle_images.zip"},
        )

    else:
        raise HTTPException(
            status_code=400, detail="지원되지 않는 response_type입니다."
        )
