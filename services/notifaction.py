import logging

import aiohttp
import pytz
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


async def fetch_owner_telephones():
    api_url = "http://localhost:8000/api/telephones/"

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
        logger.error(f"Error fetching telephones from API: {e}")
        return []


async def send_monthly_notification(bot):
    message = "🔔 Сегодня первое число месяца\nВы получили оплату за прошлый месяц?"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="payment_yes")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="payment_no")],
        ]
    )

    telephones = await fetch_owner_telephones()

    for telephone in telephones:
        try:
            chat_id = telephone.get("chat_id")
            number = telephone.get("number", "Unknown")
            is_owner = telephone.get("is_owner", False)

            if is_owner and chat_id:
                await bot.send_message(
                    chat_id=chat_id, text=message, reply_markup=keyboard
                )
                logger.info(f"Опрос отправлен {number}")
        except Exception as e:
            number = telephone.get("number", "Unknown")
            logger.error(f"Ошибка при отправке опроса {number}: {e}")


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Bishkek"))

    scheduler.add_job(
        send_monthly_notification,
        CronTrigger(day="25", hour="20", minute="45"),
        kwargs={"bot": bot},
    )

    logger.info("Планировщик настроен для отправки ежемесячных уведомлений")
    return scheduler
