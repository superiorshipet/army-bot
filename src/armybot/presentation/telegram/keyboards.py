from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def access_decision_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Approve", callback_data=f"access:approve:{telegram_id}"),
                InlineKeyboardButton(text="Reject", callback_data=f"access:reject:{telegram_id}"),
            ]
        ]
    )
