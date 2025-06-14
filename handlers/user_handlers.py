import os
import uuid
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from fsm.fsm import UserState
from handlers.utils import (
    download_file,
    get_shop_by_phone,
    get_user_profile,
    save_file_to_post,
    save_report,
    save_user_profile,
)
from keyboards.keyboards import (
    get_contact_keyboard,
    get_location_keyboard,
    get_main_keyboard,
    get_photo_keyboard,
    get_photo_type_keyboard,
)
from services.logger import logger

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    logger.info(f"/start от {message.from_user.id} ({message.from_user.full_name})")
    await message.answer(
        "👋 Привет! Я бот для загрузки фотографий магазинов.\n\n"
        "Для начала работы, пожалуйста, поделитесь своим контактом, "
        "чтобы я мог проверить ваш номер телефона в системе.",
        reply_markup=get_contact_keyboard(),
    )
    await state.set_state(UserState.unauthorized)


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "📋 <b>Инструкция по использованию бота:</b>\n\n"
        "1. Отправьте свой контакт для авторизации\n"
        "2. После успешной авторизации нажмите на кнопку «Загрузить фото»\n"
        "3. Введите название магазина для фотографии\n"
        "4. Отправьте геолокацию для привязки к фотографии\n"
        "5. Загрузите фотографию магазина\n\n"
        "Если у вас возникли проблемы, обратитесь к администратору."
    )


@router.message(Command("profile"))
@router.message(F.text == "👤 Мой профиль")
async def cmd_profile(message: Message, state: FSMContext):
    user = await get_user_profile(message.from_user.id)
    if not user:
        await message.answer(
            "Вы еще не авторизованы. Пожалуйста, поделитесь своим контактом для авторизации.",
            reply_markup=get_contact_keyboard(),
        )
        await state.set_state(UserState.unauthorized)
        return

    shop = await get_shop_by_phone(user["phone_number"])
    if shop:
        await message.answer(
            f"📊 <b>Ваш профиль:</b>\n\n"
            f"🏪 Магазин: {shop['shop_name']}\n"
            f"👤 Владелец: {shop['owner_name']}\n"
            f"📍 Адрес: {shop['address']}\n"
            f"📱 Телефон: {user['phone_number']}",
            reply_markup=get_main_keyboard(),
        )
    else:
        await message.answer(f"📱 Телефон: {user['phone_number']}\n\n❗ Этот номер не найден в системе магазинов.")


@router.message(F.content_type == ContentType.CONTACT)
async def handle_contact(message: Message, state: FSMContext):
    contact = message.contact
    phone_number = contact.phone_number
    telegram_id = message.from_user.id
    logger.info(f"Контакт от {telegram_id}: {phone_number}")

    if contact.user_id != telegram_id:
        await message.answer("Пожалуйста, отправьте свой собственный контакт.")
        return

    try:
        await save_user_profile(telegram_id, phone_number)
        await state.update_data(phone=phone_number)

        shop = await get_shop_by_phone(phone_number)

        if shop:
            await state.set_state(UserState.authorized)
            await message.answer(
                f"✅ Успешная авторизация!\n\n"
                f"Вы зарегистрированы как магазин '{shop['shop_name']}'.\n"
                f"Теперь вы можете загружать фотографии.",
                reply_markup=get_main_keyboard(),
            )
        else:
            await state.set_state(UserState.unauthorized)
            await message.answer(
                "❌ Ваш номер не найден в нашей системе.\n"
                "Обратитесь к администратору для регистрации вашего магазина."
            )
    except Exception as e:
        logger.error(f"Error in handle_contact: {e}")
        await message.answer("Произошла ошибка при проверке вашего номера. Пожалуйста, попробуйте позже.")


@router.message(UserState.authorized, F.text == "📷 Загрузить фото")
async def start_upload_photo(message: Message, state: FSMContext):
    await state.set_state(UserState.waiting_for_location)
    await message.answer(
        "Для загрузки фотографии сперва отправьте геолокацию магазина.",
        reply_markup=get_location_keyboard()
    )


@router.message(UserState.waiting_for_location, F.content_type == ContentType.LOCATION)
async def handle_location(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    user = await get_user_profile(telegram_id)

    if not user:
        await message.answer(
            "Для начала работы необходимо авторизоваться. Пожалуйста, поделитесь своим контактом.",
            reply_markup=get_contact_keyboard(),
        )
        await state.set_state(UserState.unauthorized)
        return

    await state.update_data(
        location={
            "latitude": message.location.latitude,
            "longitude": message.location.longitude,
        }
    )
    await state.set_state(UserState.waiting_for_type_photo)

    await message.answer(
        "📍 Геолокация получена!\n\nТеперь выберите тип файла.",
        reply_markup=get_photo_type_keyboard(),
    )


@router.message(UserState.waiting_for_type_photo, F.text)
async def handle_type_photo(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(UserState.authorized)
        await message.answer(
            "Возвращаемся в главное меню.",
            reply_markup=get_main_keyboard(),
        )
        return

    type_photo = message.text
    await state.update_data(type_photo=type_photo)
    await state.set_state(UserState.waiting_for_photo)

    await message.answer(
        f"📋 Тип фото: {type_photo}\n\nТеперь отправьте само фото.",
        reply_markup=get_photo_keyboard(),
    )


@router.message(UserState.waiting_for_photo, F.content_type == ContentType.DOCUMENT)
async def handle_file(message: Message, bot: Bot, state: FSMContext):
    telegram_id = message.from_user.id
    logger.info(f"Получен файл от user_id={telegram_id}")

    try:
        user_profile = await get_user_profile(telegram_id)
        if not user_profile:
            logger.warning(f"Неизвестный пользователь: {telegram_id}")
            await message.answer("Авторизуйтесь.")
            await state.set_state(UserState.unauthorized)
            return

        state_data = await state.get_data()
        location = state_data.get("location")
        type_photo = state_data.get("type_photo")

        if not location:
            logger.info(f"Нет геолокации для user_id={telegram_id}")
            await message.answer("Сначала отправьте геолокацию.")
            await state.set_state(UserState.waiting_for_location)
            return

        shop = await get_shop_by_phone(user_profile["phone_number"])
        if not shop:
            logger.warning(f"Магазин не найден: phone={user_profile['phone_number']}")
            await message.answer("Ваш магазин не зарегистрирован.")
            return

        document = message.document
        file_id = document.file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        file_name = document.file_name or f"{uuid.uuid4().hex}{os.path.splitext(file_path)[1]}"

        logger.info(f"Загрузка файла от {telegram_id}: file_id={file_id}, path={file_path}, name={file_name}")

        file_url = f"https://api.telegram.org/file/bot{os.getenv('SECRET_KEY')}/{file_path}"
        status_message = await message.answer("⏳ Загрузка файла...")

        try:
            relative_path = await download_file(file_url, file_name)

            await save_file_to_post(
                shop["id"],
                relative_path,
                latitude=location["latitude"],
                longitude=location["longitude"],
                type_photo=type_photo,
            )

            logger.info(f"Файл сохранен: {file_name} для магазина {shop['shop_name']}")

            await state.update_data(location=None, type_photo=None)
            await state.set_state(UserState.authorized)

            await bot.edit_message_text(
                f"✅ Файл успешно сохранен и связан с магазином '{shop['shop_name']}'.",
                chat_id=status_message.chat.id,
                message_id=status_message.message_id,
            )

            await message.answer(
                text='Хотите загрузить еще фото?',
                reply_markup=get_main_keyboard()
            )

        except Exception as e:
            await state.set_state(UserState.authorized)
            error_message = str(e)
            logger.exception(f"Ошибка при сохранении файла от {telegram_id}: {error_message}")

            if "более 5 минут назад" in error_message:
                await bot.edit_message_text(
                    "❌ Фото сделано более 5 минут назад. Пожалуйста, сделайте свежее фото.",
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id,
                )
            elif "EXIF данные отсутствуют" in error_message or "метаданные отсутствуют" in error_message.lower():
                await bot.edit_message_text(
                    "❌ Фото не содержит необходимые метаданные (EXIF). Пожалуйста, сделайте фото через камеру телефона.",
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id,
                )
            else:
                await bot.edit_message_text(
                    "❌ Ошибка при сохранении файла.",
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id,
                )

    except Exception as e:
        await state.set_state(UserState.authorized)
        logger.exception(f"Ошибка в handle_file от {telegram_id}: {str(e)}")
        await message.answer("❗ Неизвестная ошибка.")


@router.message(UserState.waiting_for_location, F.text == "🔙 Назад")
async def back_from_location(message: Message, state: FSMContext):
    await state.set_state(UserState.authorized)
    await message.answer(
        "Возвращаемся в главное меню.",
        reply_markup=get_main_keyboard(),
    )


@router.message(UserState.waiting_for_photo, F.text == "🔙 Назад")
async def back_from_photo(message: Message, state: FSMContext):
    await state.set_state(UserState.waiting_for_type_photo)
    await message.answer(
        "Выберите тип файла.",
        reply_markup=get_photo_type_keyboard(),
    )


@router.callback_query(lambda c: c.data in ["payment_yes", "payment_no"])
async def handle_payment_callback(callback_query):
    callback_data = callback_query.data
    user_chat_id = callback_query.from_user.id
    user = await get_user_profile(user_chat_id)

    try:
        shop = await get_shop_by_phone(user["phone_number"])

        if callback_data == "payment_yes":
            answer = "Да"
            response_text = "✅ Спасибо! Ваш ответ записан: получили оплату"
        elif callback_data == "payment_no":
            answer = "Нет"
            response_text = "❌ Ваш ответ записан: не получили оплату"

        now = datetime.now(timezone.utc)
        current_month = now.month
        current_year = now.year
        await save_report(shop["id"], answer)
        logger.info(
            f"Создан новый отчет для магазина {shop['shop_name']} за {current_month}/{current_year}: {answer}"
        )

        await callback_query.message.edit_text(text=response_text, reply_markup=None)
        await callback_query.answer()

    except Exception as e:
        await callback_query.answer("Произошла ошибка при записи ответа")
        logger.error(f"Ошибка при обработке ответа от {user_chat_id}: {e}")


@router.message(UserState.unauthorized)
async def handle_unauthorized(message: Message, state: FSMContext):
    await message.answer(
        "Для начала работы, пожалуйста, поделитесь своим контактом.",
        reply_markup=get_contact_keyboard(),
    )


@router.message(UserState.waiting_for_location)
async def handle_waiting_location_text(message: Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, отправьте геолокацию или нажмите кнопку 'Назад'.",
        reply_markup=get_location_keyboard(),
    )


@router.message(UserState.waiting_for_type_photo)
async def handle_waiting_type_text(message: Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, выберите тип файла из предложенных вариантов.",
        reply_markup=get_photo_type_keyboard(),
    )


@router.message(UserState.waiting_for_photo)
async def handle_waiting_photo_text(message: Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, отправьте фото как документ или нажмите кнопку 'Назад'.",
        reply_markup=get_photo_keyboard(),
    )


@router.message(UserState.authorized)
async def handle_authorized_commands(message: Message, state: FSMContext):
    await message.answer(
        "Используйте кнопки меню для навигации.",
        reply_markup=get_main_keyboard(),
    )


@router.message()
async def unknown_message(message: Message, state: FSMContext):
    current_state = await state.get_state()
    user = await get_user_profile(message.from_user.id)

    if not user:
        await message.answer(
            "Для начала работы, пожалуйста, поделитесь своим контактом.",
            reply_markup=get_contact_keyboard(),
        )
        await state.set_state(UserState.unauthorized)
    else:
        if current_state is None:
            await state.set_state(UserState.authorized)

        await message.answer(
            "Используйте кнопки меню для навигации.",
            reply_markup=get_main_keyboard(),
        )