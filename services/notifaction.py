import logging
import os

import aiohttp
import pytz
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


async def fetch_owner_telephones():
    api_url = f"{os.getenv('WEB_SERVICE_URL')}/api/telephones/"

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
    message = "🔔 Вы получили оплату за прошлый месяц?"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="payment_yes")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="payment_no")],
        ]
    )

    telephones = await fetch_owner_telephones()
    for telephone in telephones:
        try:
            chat_id = telephone.get("chat_id", None)
            if chat_id:
                await bot.send_message(chat_id=chat_id, text=message, reply_markup=keyboard)
        except Exception:
            logger.error("Ошибка при отправке опроса")


async def send_weekly_notification(bot):
    message = "Напоминание: Отправьте, пожалуйста, фото полки ОРИМИ КР. \n Эскертүү: ОРИМИ КРдин текчесинин сүрөтүн жөнөтүп коесузбу, сураныч."

    telephones = await fetch_owner_telephones()
    for telephone in telephones:
        try:
            chat_id = telephone.get("chat_id", None)
            if chat_id:
                await bot.send_message(chat_id=chat_id, text=message)
        except Exception:
            logger.error("Ошибка при отправке еженедельного уведомления")


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Bishkek"))

    scheduler.add_job(
        send_monthly_notification,
        CronTrigger(day="15", hour="11", minute="0"),
        kwargs={"bot": bot},
    )

    scheduler.add_job(
        send_weekly_notification,
        CronTrigger(day_of_week="mon", hour="10", minute="0"),
        kwargs={"bot": bot},
    )

    logger.info("Планировщик настроен для отправки ежемесячных и еженедельных уведомлений")
    return scheduler
