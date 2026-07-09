"""
Elden Ring Nightreign .sl2 存档解析器 (Python)

用法:
    python relic_parser.py <存档文件.sl2>  [-o output.json] [--json]

依赖 (仅标准库):
    pip install pycryptodome  # 仅需这一个, 用于 AES-CBC 解密
"""

import json
import struct
import sys
from pathlib import Path
from typing import Optional

# ── AES 解密 ──────────────────────────────────────────────
try:
    from Crypto.Cipher import AES
except ImportError:
    print("需要安装 pycryptodome: pip install pycryptodome", file=sys.stderr)
    sys.exit(1)

# Elden Ring Nightreign 存档解密密钥 (与游戏一致)
DS2_KEY = bytes([
    0x18, 0xf6, 0x32, 0x66, 0x05, 0xbd, 0x17, 0x8a,
    0x55, 0x24, 0x52, 0x3a, 0xc0, 0xa0, 0xc6, 0x09,
])

IV_SIZE = 0x10
BND4_HEADER_LEN = 64
BND4_ENTRY_HEADER_LEN = 32
BND4_MAGIC = b"BND4"


# ── BND4 解析 ─────────────────────────────────────────────
class BND4Entry:
    """一个解密后的 BND4 条目"""
    def __init__(self, index: int, name: str, clean_data: bytes):
        self.index = index
        self.name = name
        self.clean_data = clean_data


def read_int32_le(data: bytes, offset: int) -> int:
    """从字节数组指定偏移处读取小端序 32 位整数"""
    return struct.unpack_from("<i", data, offset)[0]


def read_uint16_le(data: bytes, offset: int) -> int:
    """从字节数组指定偏移处读取小端序 16 位无符号整数"""
    return struct.unpack_from("<H", data, offset)[0]


def decrypt_aes_cbc(key: bytes, iv: bytes, encrypted: bytes) -> bytes:
    """AES-CBC 解密"""
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    return cipher.decrypt(encrypted)


def decrypt_save_file(file_path: str) -> list[BND4Entry]:
    """解密 .sl2 存档文件, 返回所有 BND4 条目"""
    file_data = Path(file_path).read_bytes()

    if not file_data.startswith(BND4_MAGIC):
        raise ValueError("无效的存档文件: 未找到 BND4 文件头")

    num_entries = read_int32_le(file_data, 12)
    entries: list[BND4Entry] = []

    for i in range(num_entries):
        pos = BND4_HEADER_LEN + BND4_ENTRY_HEADER_LEN * i
        if pos + BND4_ENTRY_HEADER_LEN > len(file_data):
            break

        entry_header = file_data[pos:pos + BND4_ENTRY_HEADER_LEN]

        # 验证条目头魔数
        if entry_header[:8] != bytes([0x40, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff]):
            continue

        entry_size = read_int32_le(entry_header, 8)
        data_offset = read_int32_le(entry_header, 16)
        # footer_length = read_int32_le(entry_header, 24)

        if entry_size <= 0 or entry_size > 1_000_000_000:
            continue
        if data_offset <= 0 or data_offset + entry_size > len(file_data):
            continue

        encrypted_data = file_data[data_offset:data_offset + entry_size]
        iv = encrypted_data[:IV_SIZE]
        encrypted_payload = encrypted_data[IV_SIZE:]

        try:
            decrypted = decrypt_aes_cbc(DS2_KEY, iv, encrypted_payload)
            clean_data = decrypted[4:]  # 丢弃前 4 字节
            name = f"USERDATA_{i:02d}"
            entries.append(BND4Entry(i, name, clean_data))
        except Exception as e:
            print(f"  [警告] 解密条目 #{i} 失败: {e}", file=sys.stderr)

    return entries


# ── 遗物解析 ─────────────────────────────────────────────
class RelicSlot:
    """单个遗物槽位"""
    __slots__ = ("slot_id", "item_id", "effect_ids", "debuff_ids",
                 "sort_key", "color", "item_name")

    def __init__(self, slot_id: int, item_id: int):
        self.slot_id = slot_id
        self.item_id = item_id
        self.effect_ids: list[int] = []
        self.debuff_ids: list[int] = []
        self.sort_key: int = 0
        self.color: str = "?"
        self.item_name: str = "?"

    def to_dict(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "item_id": self.item_id,
            "item_name": self.item_name,
            "color": self.color,
            "effects": self.effect_ids,
            "debuffs": self.debuff_ids,
            "sort_key": self.sort_key,
        }


def find_hex_offset(data: bytes, hex_pattern: str, start_offset: int = 0) -> int | None:
    """在字节数组中搜索 hex 模式, 返回偏移量"""
    pattern = bytes.fromhex(hex_pattern.replace(" ", ""))
    idx = data.find(pattern, start_offset)
    return idx if idx != -1 else None


def name_bytes_to_str(name_bytes: bytes) -> str:
    """UTF-16LE 字节转字符串"""
    return name_bytes.decode("utf-16-le")


def parse_names(names_entry: BND4Entry) -> list[bytes]:
    """从条目 10 提取 10 个角色名称的原始字节"""
    data = names_entry.clean_data
    names: list[bytes] = []
    search_offset = 0

    for _ in range(10):
        # 搜索模式: 27 00 00 46 41 43 45 (= "'" + "FACE")
        offset = find_hex_offset(data, "27000046414345", search_offset)
        if offset is None:
            break

        search_offset = offset + 7
        name_start = offset - 51

        # 搜索 UTF-16LE 字符串结束符 00 00
        terminator = find_hex_offset(data, "0000", name_start)
        if terminator is None:
            break

        name_bytes = data[name_start:terminator]
        # 对齐: UTF-16LE 字符串必须是偶数长度
        if len(name_bytes) % 2 != 0:
            name_bytes = data[name_start:terminator + 1]
        names.append(name_bytes)

    return names


def parse_relics(entry_data: bytes, start_bound: int, end_bound: int,
                 sort_key_search_start: int) -> list[RelicSlot]:
    """从解密后的条目数据中解析遗物槽位"""
    # 截取搜索范围
    search_data = entry_data[start_bound:end_bound]
    relics: list[RelicSlot] = []

    # 有效槽位标记
    VALID_B3 = {0x80, 0x81, 0x82, 0x83, 0x84, 0x85}
    VALID_B4 = {0x80, 0x90, 0xC0}

    def get_slot_size(b4: int) -> int | None:
        if b4 == 0xC0:
            return 80
        elif b4 == 0x90:
            return 16
        elif b4 == 0x80:
            return 80
        return None

    def is_valid_slot(pos: int) -> tuple[bool, int | None]:
        if pos + 4 > len(search_data):
            return False, None
        b3 = search_data[pos + 2]
        b4 = search_data[pos + 3]
        if b3 in VALID_B3 and b4 in VALID_B4:
            size = get_slot_size(b4)
            if size and pos + size <= len(search_data):
                return True, size
        return False, None

    # 找到第一个有效槽位 (验证对齐)
    start_offset: int | None = None
    for i in range(len(search_data) - 8):
        valid, slot_size = is_valid_slot(i)
        if valid and slot_size:
            next_pos = i + slot_size
            valid_next, _ = is_valid_slot(next_pos)

            # 或者下一个是空槽 (前 4 字节 0x00, 后 4 字节 0xFF)
            is_empty = (
                next_pos + 8 <= len(search_data)
                and all(b == 0x00 for b in search_data[next_pos:next_pos + 4])
                and all(b == 0xFF for b in search_data[next_pos + 4:next_pos + 8])
            )

            if valid_next or is_empty:
                start_offset = i
                break

    if start_offset is None:
        return relics

    # 遍历所有槽位
    i = start_offset
    while i < len(search_data) - 4:
        b3 = search_data[i + 2]
        b4 = search_data[i + 3]

        if b3 in VALID_B3 and b4 in VALID_B4:
            size = get_slot_size(b4)
            if size and i + size <= len(search_data):
                if b4 == 0xC0:
                    slot_data = search_data[i:i + size]

                    # 读取字段
                    slot_id = struct.unpack_from("<I", slot_data, 0)[0]
                    # item_id 是 3 字节, 小端序
                    item_id_bytes = slot_data[4:7]
                    item_id = struct.unpack("<I", item_id_bytes + b"\x00")[0]

                    # 4 个效果 ID (偏移 16-31)
                    effect_ids = []
                    for off in (16, 20, 24, 28):
                        eid = struct.unpack_from("<i", slot_data, off)[0]
                        if eid != -1:
                            effect_ids.append(eid)

                    # 4 个减益 ID (偏移 56-71)
                    debuff_ids = []
                    for off in (56, 60, 64, 68):
                        did = struct.unpack_from("<i", slot_data, off)[0]
                        if did != -1:
                            debuff_ids.append(did)

                    relic = RelicSlot(slot_id, item_id)
                    relic.effect_ids = effect_ids
                    relic.debuff_ids = debuff_ids
                    relics.append(relic)

                i += size
                continue

        # 跳过空槽 (8 字节: 前 4 字节 0x00, 后 4 字节 0xFF)
        if i + 8 <= len(search_data):
            empty_bytes = search_data[i:i + 8]
            if (all(b == 0x00 for b in empty_bytes[:4])
                    and all(b == 0xFF for b in empty_bytes[4:])):
                i += 8
                continue

        i += 1

    # 查找排序键
    for relic in relics:
        # 构造搜索模式: id_bytes + 01000000
        id_bytes = struct.pack("<I", relic.slot_id)
        search_pattern = id_bytes.hex() + "01000000"

        offset = find_hex_offset(entry_data, search_pattern, sort_key_search_start)
        if offset is not None:
            relic.sort_key = read_uint16_le(entry_data, offset + 8)

    # 按排序键降序排列
    relics.sort(key=lambda r: r.sort_key, reverse=True)

    return relics


def parse_character_slot(name_bytes: bytes, entry: BND4Entry) -> dict:
    """解析单个角色槽位, 返回 {name, relics}"""
    name = name_bytes_to_str(name_bytes)
    data = entry.clean_data

    # 在数据中定位角色名
    name_hex = name_bytes.hex()
    name_offset = find_hex_offset(data, name_hex)

    if name_offset is None:
        return {"name": name, "relics": []}

    # 搜索结束标记 FF FF FF FF (从 name_offset+1000 开始)
    end_offset = find_hex_offset(data, "FFFFFFFF", name_offset + 1000)

    if end_offset is None:
        return {"name": name, "relics": []}

    relics = parse_relics(
        data,
        start_bound=32,
        end_bound=name_offset - 100,
        sort_key_search_start=end_offset,
    )
    return {"name": name, "relics": relics}


def parse_save_file(file_path: str) -> dict:
    """完整解析存档文件, 返回结构化数据"""
    print(f"Decrypting: {file_path}")
    bnd4_entries = decrypt_save_file(file_path)
    print(f"  Decrypted {len(bnd4_entries)} BND4 entries")

    if len(bnd4_entries) < 11:
        raise ValueError(f"Not enough BND4 entries (need 11, got {len(bnd4_entries)})")

    # 解析角色名称 (条目 10)
    name_bytes_list = parse_names(bnd4_entries[10])
    print(f"  Parsed {len(name_bytes_list)} character names")

    # 解析每个角色槽位 (条目 0-9)
    slots = []
    total_relics = 0
    for i in range(min(len(name_bytes_list), 10)):
        slot = parse_character_slot(name_bytes_list[i], bnd4_entries[i])
        slots.append(slot)
        total_relics += len(slot["relics"])
        relic_count = len(slot["relics"])
        if relic_count > 0:
            print(f"  Slot {i} [{slot['name']}]: {relic_count} relics")

    print(f"  Total: {total_relics} relics")

    return {
        "file": Path(file_path).name,
        "slots": [
            {
                "index": i,
                "name": s["name"],
                "relic_count": len(s["relics"]),
                "relics": [r.to_dict() for r in s["relics"]],
            }
            for i, s in enumerate(slots)
        ],
    }


def format_simple(data: dict) -> str:
    """每行一个遗物: item_id:buff1,buff2:debuff1,debuff2"""
    lines = []
    for slot in data["slots"]:
        if slot["relic_count"] == 0:
            continue
        lines.append(f"# [{slot['name']}]")
        for r in slot["relics"]:
            buffs = ",".join(str(e) for e in r["effects"])
            debuffs = ",".join(str(d) for d in r["debuffs"])
            if debuffs:
                lines.append(f"{r['item_id']}:{buffs}:{debuffs}")
            else:
                lines.append(f"{r['item_id']}:{buffs}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Elden Ring Nightreign .sl2 存档解析器",
    )
    parser.add_argument("savefile", help=".sl2 存档文件路径")
    parser.add_argument("-o", "--output", help="输出文件路径 (默认 stdout)")
    parser.add_argument("-f", "--format", choices=["json", "simple"], default="json",
                        help="输出格式: json 或 simple (默认 json)")
    parser.add_argument("--compact", action="store_true",
                        help="JSON 模式下紧凑输出 (去掉换行缩进)")
    args = parser.parse_args()

    try:
        result = parse_save_file(args.savefile)

        if args.format == "simple":
            output = format_simple(result)
        else:
            indent = None if args.compact else 2
            output = json.dumps(result, ensure_ascii=False, indent=indent)

        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Output: {args.output}")
        else:
            print(output)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
