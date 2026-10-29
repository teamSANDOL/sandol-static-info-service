"""한국공학대학교 iBook 뷰어에서 책 코드·원본 파일·페이지 이미지를 내려받는 도구."""

import os
import json
from typing import Optional
from xml.etree import ElementTree
import httpx
from app.config import Config, logger


class FetchError(Exception):
    """iBook 요청이 실패했을 때 올리는 예외."""

    def __init__(self, status_code=None, message="파일 처리중 오류가 발생했습니다."):
        """상태 코드가 있으면 메시지 뒤에 붙여 예외를 만듭니다.

        Args:
            status_code: 실패한 HTTP 상태 코드. 응답 자체가 없었으면 None.
            message: 사람이 읽을 오류 설명.
        """
        self.status_code = status_code
        self.message = (
            message if status_code is None else f"{message} Status code: {status_code}"
        )
        super().__init__(self.message)


class BookDownloader:
    """한국공학대학교 iBook에서 파일을 비동기로 다운로드하는 클래스입니다."""

    def __init__(
        self,
        url: str = "https://ibook.tukorea.ac.kr/Viewer/menu02",
        file_list_url: str = "https://ibook.tukorea.ac.kr/web/RawFileList",
        image_save_path: str = "images",
    ):
        """다운로더를 설정합니다.

        Args:
            url: iBook 뷰어 주소.
            file_list_url: 원본 파일 목록 API 주소.
            image_save_path: download_images의 기본 저장 디렉터리.
        """
        self.url = url
        self.file_list_url = file_list_url
        self.image_save_path = image_save_path
        self.bookcode = None
        self.file_name = None
        self.headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://ibook.tukorea.ac.kr",
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
        }

    async def fetch_bookcode(self):
        """뷰어 페이지에서 bookcode를 읽어 저장합니다.

        Returns:
            bookcode 문자열.

        Raises:
            FetchError: 페이지 요청이 실패하거나 bookcode가 없을 때.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(self.url, timeout=10)
            if response.status_code != Config.HttpStatus.OK:
                raise FetchError(response.status_code, "bookcode 요청 실패")

            for line in response.text.splitlines():
                if "var bookcode =" in line:
                    self.bookcode = line.split("=")[1].strip().strip(";").strip("'")
                    logger.info(f"[BookDownloader] bookcode: {self.bookcode}")
                    return self.bookcode

        raise FetchError(None, "bookcode를 찾을 수 없습니다.")

    async def fetch_file_list(self) -> str:
        """원본 파일 목록 XML을 가져옵니다. bookcode가 없으면 먼저 읽습니다.

        Returns:
            파일 목록 XML 원문.

        Raises:
            FetchError: 목록 요청이 실패했을 때.
        """
        if self.bookcode is None:
            await self.fetch_bookcode()

        data = {"key": "kpu", "bookcode": self.bookcode, "base64": "N"}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.file_list_url,
                headers=self.headers,
                data=data,
                timeout=10,
            )
            if response.status_code != Config.HttpStatus.OK:
                raise FetchError(response.status_code, "파일 목록 요청 실패")
            return response.text

    def get_file_url(self, file_list_content: str) -> str:
        """파일 목록 XML에서 첫 파일의 다운로드 주소를 만듭니다.

        file_name 속성도 이 호출로 갱신됩니다.

        Args:
            file_list_content: fetch_file_list가 돌려준 XML.

        Returns:
            원본 파일 URL.

        Raises:
            FetchError: 목록에 파일이 없을 때.
        """
        root = ElementTree.fromstring(file_list_content)
        for file in root.findall("file"):
            file_name = file.attrib["name"]
            self.file_name = file_name
            file_url = file.attrib.get("file_url")
            if file_url:
                return file_url

            host = file.attrib["host"]
            bookcode = root.attrib["bookcode"]
            return f"https://{host}/contents/{bookcode[0]}/{bookcode[:3]}/{bookcode}/raw/{file_name}"
        raise FetchError(None, "파일 URL을 찾을 수 없습니다.")

    async def download_file(self, file_url: str, save_as: str):
        """파일을 내려받아 저장합니다.

        Args:
            file_url: 내려받을 주소.
            save_as: 저장할 경로.

        Raises:
            FetchError: 다운로드 응답이 성공이 아닐 때.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(file_url, timeout=10)
            if response.status_code != Config.HttpStatus.OK:
                raise FetchError(response.status_code, "파일 다운로드 실패")
            with open(save_as, "wb") as f:
                f.write(response.content)
        logger.info(f"[BookDownloader] 파일 저장 완료 → {save_as}")

    async def fetch_image_list(self) -> list[str]:
        """페이지 이미지 URL 목록을 가져옵니다. bookcode가 없으면 먼저 읽습니다.

        Returns:
            페이지 순서대로의 이미지 URL 목록.

        Raises:
            FetchError: 요청이 실패하거나 응답이 JSON이 아닐 때.
        """
        if self.bookcode is None:
            await self.fetch_bookcode()

        json_url = f"{self.url.rsplit('/', 1)[0]}/getBookXML/{self.bookcode}"
        async with httpx.AsyncClient() as client:
            response = await client.get(json_url, headers=self.headers, timeout=10)
            if response.status_code != Config.HttpStatus.OK:
                raise FetchError(
                    response.status_code, "이미지 목록을 가져오지 못했습니다."
                )

            try:
                image_data = response.json()
                return ["https:" + img["src"].replace("\\/", "/") for img in image_data]
            except json.JSONDecodeError as e:
                raise FetchError(None, f"JSON 파싱 오류: {e}") from e

    async def download_images(self, image_save_path: Optional[str] = None):
        """모든 페이지 이미지를 page_{n}.jpg로 저장합니다. 개별 실패는 경고만 남기고 넘어갑니다.

        Args:
            image_save_path: 저장 디렉터리. None이면 생성자에서 받은 경로.
        """
        if image_save_path is None:
            image_save_path = self.image_save_path

        await self.fetch_bookcode()
        image_urls = await self.fetch_image_list()

        if not image_urls:
            logger.info("다운로드할 이미지가 없습니다.")
            return

        os.makedirs(image_save_path, exist_ok=True)

        async with httpx.AsyncClient() as client:
            for idx, url in enumerate(image_urls, start=1):
                save_as = os.path.join(image_save_path, f"page_{idx}.jpg")
                response = await client.get(url, headers=self.headers, timeout=10)

                if response.status_code == Config.HttpStatus.OK:
                    with open(save_as, "wb") as f:
                        f.write(response.content)
                    logger.info(f"이미지 저장 완료: {save_as}")
                else:
                    logger.warning(
                        f"다운로드 실패: {url}, 상태 코드: {response.status_code}"
                    )

    async def get_file(self, file_name: Optional[str] = None):
        """원본 파일 하나를 찾아 내려받습니다.

        Args:
            file_name: 저장 경로. None이면 목록의 파일명 또는 기본 경로.
        """
        file_name = file_name or self.file_name or "/tmp/data.xlsx"
        await self.fetch_bookcode()
        file_list = await self.fetch_file_list()
        file_url = self.get_file_url(file_list)
        await self.download_file(file_url, file_name)
