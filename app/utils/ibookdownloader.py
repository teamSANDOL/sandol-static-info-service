import os
import json
from typing import Optional
from xml.etree import ElementTree
import httpx
from app.config.config import logger


class FetchError(Exception):
    def __init__(self, status_code=None, message="파일 처리중 오류가 발생했습니다."):
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
        async with httpx.AsyncClient() as client:
            response = await client.get(self.url, timeout=10)
            if response.status_code != 200:
                raise FetchError(response.status_code, "bookcode 요청 실패")

            for line in response.text.splitlines():
                if "var bookcode =" in line:
                    self.bookcode = line.split("=")[1].strip().strip(";").strip("'")
                    logger.info(f"[BookDownloader] bookcode: {self.bookcode}")
                    return self.bookcode

        raise FetchError(None, "bookcode를 찾을 수 없습니다.")

    async def fetch_file_list(self) -> str:
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
            if response.status_code != 200:
                raise FetchError(response.status_code, "파일 목록 요청 실패")
            return response.text

    def get_file_url(self, file_list_content: str) -> str:
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
        async with httpx.AsyncClient() as client:
            response = await client.get(file_url, timeout=10)
            if response.status_code != 200:
                raise FetchError(response.status_code, "파일 다운로드 실패")
            with open(save_as, "wb") as f:
                f.write(response.content)
        logger.info(f"[BookDownloader] 파일 저장 완료 → {save_as}")

    async def fetch_image_list(self) -> list[str]:
        if self.bookcode is None:
            await self.fetch_bookcode()

        json_url = f"{self.url.rsplit('/', 1)[0]}/getBookXML/{self.bookcode}"
        async with httpx.AsyncClient() as client:
            response = await client.get(json_url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                raise FetchError(
                    response.status_code, "이미지 목록을 가져오지 못했습니다."
                )

            try:
                image_data = response.json()
                return ["https:" + img["src"].replace("\\/", "/") for img in image_data]
            except json.JSONDecodeError as e:
                raise FetchError(None, f"JSON 파싱 오류: {e}") from e

    async def download_images(self, image_save_path: Optional[str] = None):
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

                if response.status_code == 200:
                    with open(save_as, "wb") as f:
                        f.write(response.content)
                    logger.info(f"이미지 저장 완료: {save_as}")
                else:
                    logger.warning(
                        f"다운로드 실패: {url}, 상태 코드: {response.status_code}"
                    )

    async def get_file(self, file_name: Optional[str] = None):
        file_name = file_name or self.file_name or "/tmp/data.xlsx"
        await self.fetch_bookcode()
        file_list = await self.fetch_file_list()
        file_url = self.get_file_url(file_list)
        await self.download_file(file_url, file_name)
