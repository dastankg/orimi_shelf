import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import piexif
from asgiref.sync import sync_to_async
from PIL import Image

from config.redis_connect import redis_client
from services.logger import logger


async def get_user_profile(telegram_id: int) -> dict[str, Any] | None:
    key = f"user:{telegram_id}"
    data = await redis_client.get(key)
    return json.loads(data) if data else None


async def get_shop_by_phone(phone_number: str):
    api_url = f"http://localhost:8000/api/shops/{phone_number}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"API request failed with status {response.status}")
                    return []
    except Exception as e:
        logger.error(f"Error in get_shop_by_phone: {e}")
        return None


async def save_user_profile(telegram_id: int, phone_number: str) -> bool:
    key = f"user:{telegram_id}"
    user_data = {"phone_number": phone_number}
    await redis_client.set(key, json.dumps(user_data))
    try:
        api_url = f"http://localhost:8000/api/telephones-get/{phone_number}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if "id" in data:
                        id = data["id"]
                        api_url = f"http://localhost:8000/api/telephones/{id}"
                        update_data = {"telegram_id": telegram_id}
                        async with session.patch(
                            api_url, json=update_data
                        ) as update_response:
                            if update_response.status == 200:
                                logger.info(
                                    f"Successfully updated telegram_id for phone {phone_number}"
                                )
                                return True
                            else:
                                logger.error(
                                    f"Failed to update telegram_id. Status: {update_response.status}"
                                )
                                return False
                    return False
                else:
                    logger.error(f"API request failed with status {response.status}")
                    return False

    except Exception as e:
        logger.error(f"Error saving user profile to Redis: {e}")
        return False


def check_photo_creation_time(file_path):
    try:
        file_extension = os.path.splitext(file_path.lower())[1]

        if file_extension == ".heic":
            metadata = get_heic_metadata(file_path)
            if not metadata:
                logger.warning(f"Метаданные отсутствуют в HEIC файле: {file_path}")
                return False

            date_time_str = None
            for field in ["DateTimeOriginal", "CreateDate"]:
                if field in metadata and metadata[field]:
                    date_time_str = metadata[field]
                    break

            if not date_time_str:
                logger.warning(
                    f"Данные о времени создания отсутствуют в HEIC: {file_path}"
                )
                return False

            match = re.match(
                r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", date_time_str
            )
            if not match:
                logger.warning(f"Неизвестный формат даты в HEIC: {date_time_str}")
                return False

            year, month, day, hour, minute, second = map(int, match.groups())
            photo_time = datetime(year, month, day, hour, minute, second)

            current_time = datetime.now()
            time_diff = current_time - photo_time

            return time_diff <= timedelta(minutes=3)

        else:
            try:
                img = Image.open(file_path)

                if not hasattr(img, "_getexif") or not img._getexif():
                    logger.warning(
                        f"EXIF данные отсутствуют в изображении: {file_path}"
                    )
                    return False

                exif_dict = piexif.load(img.info["exif"])

                if "0th" in exif_dict and piexif.ImageIFD.DateTime in exif_dict["0th"]:
                    date_time_str = exif_dict["0th"][piexif.ImageIFD.DateTime].decode(
                        "utf-8"
                    )
                    photo_time = datetime.strptime(date_time_str, "%Y:%m:%d %H:%M:%S")

                    current_time = datetime.now()
                    time_diff = current_time - photo_time

                    return time_diff <= timedelta(minutes=3)
                else:
                    logger.warning(
                        f"Данные о времени создания отсутствуют в EXIF: {file_path}"
                    )
                    return False

            except Exception as e:
                logger.warning(f"Ошибка при чтении EXIF данных: {e}")
                return False

    except Exception as e:
        logger.error(f"Ошибка при проверке времени создания файла: {e}")
        return False


def get_heic_metadata(file_path):
    try:
        try:
            subprocess.run(["exiftool", "-ver"], capture_output=True, check=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.error(
                "ExifTool не установлен. Установите с помощью 'sudo dnf install perl-Image-ExifTool'"
            )
            return None

        result = subprocess.run(
            ["exiftool", "-json", "-DateTimeOriginal", "-CreateDate", file_path],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error(f"Ошибка при выполнении exiftool: {result.stderr}")
            return None

        metadata = json.loads(result.stdout)
        if not metadata or len(metadata) == 0:
            return None

        return metadata[0]
    except Exception as e:
        logger.error(f"Ошибка при чтении метаданных HEIC: {e}")
        return None


async def download_file(file_url: str, filename: str):
    try:
        os.makedirs("media/documents", exist_ok=True)

        save_path = f"media/documents/{filename}"
        relative_path = f"documents/{filename}"

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as response:
                if response.status != 200:
                    raise Exception(f"Failed to download file: {response.status}")

                with open(save_path, "wb") as f:
                    f.write(await response.read())

        file_extension = os.path.splitext(filename.lower())[1]
        image_extensions = [".jpg", ".jpeg", ".png", ".heic", ".tiff", ".bmp"]

        if any(file_extension == ext for ext in image_extensions):
            is_valid = await sync_to_async(check_photo_creation_time)(save_path)
            if not is_valid:
                if os.path.exists(save_path):
                    os.remove(save_path)
                raise Exception(
                    "Фото не содержит необходимые метаданные или было сделано более 3 минут назад."
                )

        return relative_path
    except Exception as e:
        logger.error(f"Error in download_file: {e}")
        raise


async def get_address_from_coordinates(latitude, longitude):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": latitude,
                    "lon": longitude,
                    "format": "json",
                },
                headers={"User-Agent": "DjangoApp"},
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("display_name")
        return None
    except Exception as e:
        logger.error(f"Error in get_address_from_coordinates: {e}")
        return None
