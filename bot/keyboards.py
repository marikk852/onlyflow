from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def duplicate_action_kb(batch_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✓ Загрузить всё равно", callback_data=f"dup:all:{batch_id}")
    builder.button(text="✗ Пропустить дубликаты", callback_data=f"dup:skip:{batch_id}")
    builder.button(text="⊘ Отменить", callback_data=f"dup:cancel:{batch_id}")
    builder.adjust(1)
    return builder.as_markup()


def gallery_select_kb(accounts_galleries: list[dict], batch_id: int) -> InlineKeyboardMarkup:
    """
    accounts_galleries = [
        {"account_id": 1, "account_name": "Alina_Official", "galleries": [{"id": 1, "name": "VIP Фото"}, ...]},
        ...
    ]
    """
    builder = InlineKeyboardBuilder()
    for ag in accounts_galleries:
        for g in ag["galleries"]:
            builder.button(
                text=f"{ag['account_name']} → {g['name']}",
                callback_data=f"selgal:{batch_id}:{ag['account_id']}:{g['id']}"
            )
    builder.button(text="⚡ Запустить загрузку", callback_data=f"launch:{batch_id}")
    builder.button(text="⊘ Отмена", callback_data=f"cancel:{batch_id}")
    builder.adjust(1)
    return builder.as_markup()


def launch_kb(batch_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚡ Запустить загрузку", callback_data=f"launch:{batch_id}")
    builder.button(text="⊘ Отмена", callback_data=f"cancel:{batch_id}")
    builder.adjust(2)
    return builder.as_markup()


def confirm_delete_kb(entity: str, entity_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✓ Да, удалить", callback_data=f"confirm_delete:{entity}:{entity_id}")
    builder.button(text="✗ Отмена", callback_data="cancel_delete")
    builder.adjust(2)
    return builder.as_markup()
