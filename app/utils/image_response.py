from typing import Union
from fastapi.responses import (
    JSONResponse,
    StreamingResponse,
    PlainTextResponse,
)
from fastapi import HTTPException
import httpx, base64, io, zipfile

from app.config import Config


async def build_response_json(image_urls: list[str]):
    """Json 반환 값 생성하는 함수입니다.

    Args:
        image_urls (list[str]): 이미지 URL 리스트

    Returns:
        JSONResponse: 이미지 URL 리스트를 포함한 JSON 응답
    """
    return JSONResponse(content={"image_urls": image_urls})


async def build_response_base64(image_urls: list[str]):
    """Base64 인코딩된 이미지 반환 값 생성하는 함수입니다.

    Args:
        image_urls (list[str]): 이미지 URL 리스트

    Returns:
        JSONResponse: Base64로 인코딩된 이미지 데이터를 포함한 JSON 응답
    """
    base64_list = []
    async with httpx.AsyncClient() as client:
        for url in image_urls:
            resp = await client.get(url, timeout=10)
            if resp.status_code != Config.HttpStatus.OK:
                raise HTTPException(
                    status_code=Config.HttpStatus.BAD_GATEWAY,
                    detail=f"이미지 다운로드 실패: {url}",
                )
            encoded = base64.b64encode(resp.content).decode("utf-8")
            base64_list.append(encoded)
    return JSONResponse(
        content={"image_base64_list": base64_list}
        if len(base64_list) > 1
        else {"image_base64": base64_list[0]}
    )


async def build_response_zip(image_urls: list[str]):
    """Zip 파일 반환 값 생성하는 함수입니다.

    Args:
        image_urls (list[str]): 이미지 URL 리스트

    Returns:
        StreamingResponse: 이미지들을 포함한 ZIP 파일 스트리밍 응답
    """
    zip_buffer = io.BytesIO()
    async with httpx.AsyncClient() as client:
        with zipfile.ZipFile(zip_buffer, "w") as zip_file:
            for idx, url in enumerate(image_urls, 1):
                resp = await client.get(url, timeout=10)
                if resp.status_code != Config.HttpStatus.OK:
                    raise HTTPException(
                        status_code=Config.HttpStatus.BAD_GATEWAY,
                        detail=f"이미지 다운로드 실패: {url}",
                    )
                zip_file.writestr(f"shuttle_{idx}.jpg", resp.content)
    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=shuttle_images.zip"},
    )


async def build_response_octet_stream(image_urls: list[str]):
    """Octet-stream 반환 값 생성하는 함수입니다.

    Args:
        image_urls (list[str]): 이미지 URL 리스트

    Returns:
        StreamingResponse: 단일 이미지 또는 ZIP 파일 스트리밍 응답
    """
    if len(image_urls) == 1:
        async with httpx.AsyncClient() as client:
            resp = await client.get(image_urls[0], timeout=10)
            if resp.status_code != Config.HttpStatus.OK:
                raise HTTPException(
                    status_code=Config.HttpStatus.BAD_GATEWAY,
                    detail="이미지 다운로드 실패",
                )
            return StreamingResponse(
                iter([resp.content]),
                media_type="image/jpeg",
                headers={"Content-Disposition": "inline; filename=shuttle.jpg"},
            )
    else:
        return await build_response_zip(image_urls)


async def build_response_text(image_urls: list[str]):
    """Plain text 반환 값 생성하는 함수입니다.

    Args:
        image_urls (list[str]): 이미지 URL 리스트

    Returns:
        PlainTextResponse: 첫 번째 이미지 URL을 포함한 텍스트 응답
    """
    return PlainTextResponse(content=image_urls[0])


async def build_response_jpeg(image_urls: list[str]):
    """JPEG 이미지 반환 값 생성하는 함수입니다.

    Args:
        image_urls (list[str]): 이미지 URL 리스트

    Returns:
        StreamingResponse: JPEG 이미지 스트리밍 응답
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(image_urls[0], timeout=10)
        if resp.status_code != Config.HttpStatus.OK:
            raise HTTPException(
                status_code=Config.HttpStatus.BAD_GATEWAY, detail="이미지 다운로드 실패"
            )
        return StreamingResponse(
            iter([resp.content]),
            media_type="image/jpeg",
            headers={"Content-Disposition": "inline; filename=shuttle.jpg"},
        )


async def build_image_response(image_urls: Union[str, list[str]], response_type: str):
    """이미지 응답 생성하는 함수입니다.

    Args:
        image_urls (Union[str, list[str]]): 이미지 URL 또는 URL 리스트
        response_type (str): 응답 타입 (json, base64, zip, octet-stream, text, jpeg, png)

    Returns:
        Response: 요청된 타입에 따른 FastAPI 응답 객체
    """
    urls = [image_urls] if isinstance(image_urls, str) else image_urls

    if not image_urls:
        raise HTTPException(
            status_code=Config.HttpStatus.NOT_FOUND, detail="이미지 없음"
        )

    if response_type == "json":
        return await build_response_json(urls)
    if response_type == "base64":
        return await build_response_base64(urls)
    if response_type == "zip":
        return await build_response_zip(urls)
    if response_type == "octet-stream":
        return await build_response_octet_stream(urls)
    if response_type == "text":
        return await build_response_text(urls)
    if response_type == "jpeg":
        return await build_response_jpeg(urls)
    raise HTTPException(status_code=400, detail="지원되지 않는 response_type입니다.")
