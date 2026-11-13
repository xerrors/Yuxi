import re
import time

from pypinyin import Style, lazy_pinyin


def to_pinyin(text: str) -> str:
    return "".join(lazy_pinyin(text, style=Style.NORMAL))


def validate_username(username: str) -> tuple[bool, str]:
    if not username:
        return False, "用户名不能为空"
    if len(username) < 2:
        return False, "用户名长度不能少于2个字符"
    if len(username) > 20:
        return False, "用户名长度不能超过20个字符"
    if not re.fullmatch(r"[一-龥a-zA-Z0-9_]+", username):
        return False, "用户名只能包含中文、英文、数字和下划线"
    return True, ""


def generate_uid(username: str) -> str:
    uid = re.sub(r"[^a-zA-Z0-9_]", "", to_pinyin(username.strip()))
    if uid and uid[0].isdigit():
        uid = f"u{uid}"
    if len(uid) < 2:
        uid = f"user{hash(username) % 10000:04d}"
    return uid[:20].lower()


def generate_unique_uid(username: str, existing_uids: list[str]) -> str:
    base_uid = generate_uid(username)
    if base_uid not in existing_uids:
        return base_uid

    counter = 1
    while counter <= 9999:
        candidate = f"{base_uid}{counter}"
        if candidate not in existing_uids:
            return candidate
        counter += 1

    return f"{base_uid}{int(time.time()) % 10000}"


def is_valid_phone_number(phone: str) -> bool:
    if not phone:
        return False
    return bool(re.match(r"^1[3-9]\d{9}$", re.sub(r"[\s\-\(\)]", "", phone)))
