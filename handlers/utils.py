import asyncio
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timedelta
from typing import Any
import pillow_heif
import aiohttp
import piexif
from asgiref.sync import sync_to_async
from PIL import Image

from config.redis_connect import redis_client
from services.logger import logger


async def save_report(shop_id, ans):
    api_url = f"{os.getenv('WEB_SERVICE_URL')}/api/reports/"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json={"shop": shop_id, "answer": ans}) as response:
                if response.status == 201:
                    await response.json()
                    return
                else:
                    logger.error(f"API request failed with status {response.status}")
                    return None

    except Exception:
        return None


async def get_user_profile(telegram_id: int) -> dict[str, Any] | None:
    key = f"user:{telegram_id}"
    data = await redis_client.get(key)
    return json.loads(data) if data else None


async def get_shop_by_phone(phone_number: str):
    if not phone_number.startswith("+"):
        phone_number = "+" + phone_number
    api_url = f"{os.getenv('WEB_SERVICE_URL')}/api/shops/{phone_number}"
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
    if not phone_number.startswith("+"):
        phone_number = "+" + phone_number
    key = f"user:{telegram_id}"
    user_data = {"phone_number": phone_number}
    await redis_client.set(key, json.dumps(user_data))
    try:
        api_url = f"{os.getenv('WEB_SERVICE_URL')}/api/telephones-get/{phone_number}/"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if "id" in data:
                        id = data["id"]
                        api_url = f"{os.getenv('WEB_SERVICE_URL')}/api/telephones/{id}/"
                        update_data = {"chat_id": telegram_id}
                        async with session.patch(api_url, json=update_data) as update_response:
                            if update_response.status == 200:
                                logger.info(f"Successfully updated telegram_id for phone {phone_number}")
                                return True
                            else:
                                logger.error(f"Failed to update telegram_id. Status: {update_response.status}")
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
                logger.warning(f"Данные о времени создания отсутствуют в HEIC: {file_path}")
                return False

            match = re.match(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", date_time_str)
            if not match:
                logger.warning(f"Неизвестный формат даты в HEIC: {date_time_str}")
                return False

            year, month, day, hour, minute, second = map(int, match.groups())
            photo_time = datetime(year, month, day, hour, minute, second)

            current_time = datetime.now()
            time_diff = current_time - photo_time

            return time_diff <= timedelta(minutes=5)

        else:
            try:
                img = Image.open(file_path)

                if not hasattr(img, "_getexif") or not img._getexif():
                    logger.warning(f"EXIF данные отсутствуют в изображении: {file_path}")
                    return False

                exif_dict = piexif.load(img.info["exif"])

                if "0th" in exif_dict and piexif.ImageIFD.DateTime in exif_dict["0th"]:
                    date_time_str = exif_dict["0th"][piexif.ImageIFD.DateTime].decode("utf-8")
                    photo_time = datetime.strptime(date_time_str, "%Y:%m:%d %H:%M:%S")

                    current_time = datetime.now()
                    time_diff = current_time - photo_time

                    return time_diff <= timedelta(minutes=5)
                else:
                    logger.warning(f"Данные о времени создания отсутствуют в EXIF: {file_path}")
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
            logger.error("ExifTool не установлен. Установите с помощью 'sudo dnf install perl-Image-ExifTool'")
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
        os.makedirs("media/shelf", exist_ok=True)
        _, ext = os.path.splitext(filename)
        unique_filename = f"{uuid.uuid4()}{ext}"
        save_path = f"media/shelf/{unique_filename}"
        relative_path = f"shelf/{unique_filename}"

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
                raise Exception("Фото не содержит необходимые метаданные или было сделано более 5 минут назад.")

            if file_extension in ['.heic', '.heif']:
                new_path = await convert_heic_to_jpeg(save_path)
                relative_path = f"shelf/{os.path.basename(new_path)}"

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


async def save_file_to_post(shop_id, relative_path, latitude=None, longitude=None, type_photo=None):
    try:
        file_path = f"media/{relative_path}"
        api_url = f"{os.getenv('WEB_SERVICE_URL')}/api/shop-posts/create/"
        data = {"shop_id": shop_id, "latitude": latitude, "longitude": longitude, "post_type": type_photo}

        logger.info(f"Отправка файла: {file_path}")
        logger.info(f"Данные: {data}")

        async with aiohttp.ClientSession() as session:
            with open(file_path, "rb") as image_file:
                form_data = aiohttp.FormData()
                for key, value in data.items():
                    if value is not None:
                        form_data.add_field(key, str(value))

                form_data.add_field("image", image_file, filename=os.path.basename(file_path))

                async with session.post(api_url, data=form_data) as response:
                    response_text = await response.text()

                    # Очистка файла
                    if os.path.exists(file_path):
                        os.remove(file_path)

                    if response.status == 201:
                        logger.info("Файл успешно загружен")
                        return {"success": True, "data": json.loads(response_text) if response_text else None}
                    else:
                        logger.error(f"Ошибка при создании поста. Статус: {response.status}, Ответ: {response_text}")
                        return {"success": False, "status": response.status, "error": response_text}

    except Exception as e:
        logger.error(f"Ошибка в save_file_to_post: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return {"success": False, "error": str(e)}


async def convert_heic_to_jpeg(heic_path):
    try:
        if not heic_path.lower().endswith(('.heic', '.heif')):
            return heic_path

        jpeg_path = os.path.splitext(heic_path)[0] + '.jpg'

        try:
            pillow_heif.register_heif_opener()
            with Image.open(heic_path) as img:
                img.convert('RGB').save(jpeg_path, 'JPEG', quality=95, optimize=True)

            logger.info(f"HEIC конвертирован через pillow-heif: {heic_path} -> {jpeg_path}")

        except (ImportError, Exception) as e:
            logger.warning(f"Pillow-heif не сработал: {e}. Пробуем ImageMagick...")

            cmd = ['convert', heic_path, jpeg_path]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Неизвестная ошибка"
                raise Exception(f"ImageMagick failed: {error_msg}")

        if not os.path.exists(jpeg_path):
            raise Exception(f"Не удалось создать JPEG файл: {jpeg_path}")

        if os.path.getsize(jpeg_path) == 0:
            raise Exception("Созданный JPEG файл пустой")

        if os.path.exists(heic_path):
            os.remove(heic_path)
            logger.info(f"Удален оригинальный HEIC файл: {heic_path}")

        return jpeg_path

    except Exception as e:
        logger.error(f"Ошибка в convert_heic_to_jpeg: {e}")
        raise
