# -*- coding: utf-8 -*-

from io import BufferedReader
import logging
import os
import struct
import argparse


def simple_logger():
    def _log_level_from_env():
        return os.environ.get("PY_LOG_LEVEL", logging.INFO)

    lvl = _log_level_from_env()
    logger = logging.getLogger(__file__)
    logger.setLevel(lvl)
    handler = logging.FileHandler(filename="py.log", encoding="utf8")
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(filename)12s:%(lineno)-3d %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


LOGGER = simple_logger()


def read_utf16_str(f: BufferedReader, offset=-1, size=2):
    if offset >= 0:
        f.seek(offset)
    string = f.read(size)
    s = string.decode("UTF-16LE")
    s = s.rstrip("\0")  # string may have trailing '\0'
    LOGGER.debug("解析字符串，起始位置：%d，字符串长度：%d，字符串：%s", offset, size, s)
    return s


def read_uint16(f):
    v = struct.unpack("<H", f.read(2))
    LOGGER.debug("读取小端uint16：%d", v[0])
    return v[0]


def get_hz_offset(f):
    mask = f.read(128)[4]
    if mask == 0x44:
        return 0x2628
    elif mask == 0x45:
        return 0x26C4
    else:
        LOGGER.error("不支持的文件类型(无法获取汉语词组的偏移量)")
        raise ValueError("unknown mask: {}".format(mask))


class DictMeta:
    def __init__(self, title, category, desc, samples) -> None:
        self.title = title
        self.category = category
        self.desc = desc
        self.samples = samples

    def __repr__(self) -> str:
        return """
        题目：{}
        分类：{}
        描述：{}
        样例：{}
        """.format(self.title, self.category, self.desc, self.samples).rstrip()


def get_dict_meta(f):
    """
    获取搜狗细胞词库元信息。
    """
    title = read_utf16_str(f, 0x130, 0x338 - 0x130)
    category = read_utf16_str(f, 0x338, 0x540 - 0x338)
    desc = read_utf16_str(f, 0x540, 0xD40 - 0x540)
    samples = read_utf16_str(f, 0xD40, 0x1540 - 0xD40)
    return DictMeta(title, category, desc, samples)


def syllable_table(f):
    """
    获取全局音节表。

    汉语拼音所有的音节。
    所以可以使用 zuo 作为退出条件，因为它是所有音节中的最后一个。
    实际上也可以使用数量作为退出条件。

    音节表的结构如下：
    - 2 字节：整数，代表这个拼音的索引
    - 2 字节：整数，拼音的字节长度 -> length
    - length 字节: 当前的拼音，每个字符两个字节
    """
    syllables = {}
    f.seek(0x1540 + 4)
    cnt = 0
    while True:
        index = read_uint16(f)
        length = read_uint16(f)
        syllable = read_utf16_str(f, -1, length)
        # 按顺序排列的音节，其索引必然等于计数器
        assert index == cnt
        syllables[index] = syllable
        LOGGER.debug("索引值：%2d -> %s", index, syllable)

        cnt += 1
        if syllable == "zuo":
            LOGGER.info("读取到最后一个音节 'zuo'，停止。")
            break
    LOGGER.info("全部音节共计 %d 个。", cnt + 1)
    return syllables


def word_table(f: BufferedReader, file_size, hz_offset, syllable_table, use_ext_as_frequency):
    """
    汉语词组表，在文件中的偏移值是 0x2628 或 0x26c4
    格式为多个同音词块，一个同音词块的格式如下：
    1. 同音词数量和全拼。
        - (2 bytes           ): word_cnt      : 同音词数量
        - (2 bytes           ): syllable_cnt  : 音节索引个数
        - (syllable_cnt bytes): syllable_index: 音节索引

    2. 同音词表，每个同音词包含以下信息，并重复 word_cnt 次。
        - (2 bytes       ): char_cnt: 中文词组字节数长度
        - (char_cnt bytes): word    : 汉语词组
        - (2 bytes       ): ext_len : 可能代表扩展信息的长度，好像都是 10
        - (ext_len bytes ): ext     : 扩展信息，一共 10 个字节，前两个字节是一个整数（不知道是不是词频），
                                          后八个字节全是 0，ext_len 和 ext 一共 12 个字节

    """
    f.seek(hz_offset)
    words = []
    while f.tell() != file_size:
        word_cnt = read_uint16(f)
        syllable_cnt = read_uint16(f)
        LOGGER.debug("同音词数量：%d，音节索引数量：%d", word_cnt, syllable_cnt)
        syllables = []
        for _ in range(syllable_cnt // 2):  # read_uint16 每次读取 2 byte，所以需要除以 2
            syllable_index = read_uint16(f)
            if syllable_index not in syllable_table:
                raise ValueError("发现了未注册的拼音索引：{}".format(syllable_index))
            syllables.append(syllable_table[syllable_index])
        full_spell = " ".join(syllables)
        LOGGER.debug("获得全拼：%s", full_spell)

        for _ in range(word_cnt):
            char_cnt = read_uint16(f)
            word = read_utf16_str(f, -1, char_cnt)
            LOGGER.debug("获得词语：%s，长度：%d", word, char_cnt)

            # ext_len 和 ext 共 12 个字节
            if use_ext_as_frequency:
                f.read(2)
                freq = read_uint16(f)
                f.read(8)
                words.append((full_spell, word, str(freq)))
            else:
                f.read(12)
                words.append((full_spell, word))

    return words


def args():
    ap = argparse.ArgumentParser(description="搜狗细胞词库转写为拼音汉字对照表工具。")
    ap.add_argument("--scel", "-s", type=str, required=True, help="搜狗细胞词库文件。")
    ap.add_argument("--output", "-o", type=str, required=False, help="转写后输出的文件名，默认使用词库元信息中的标题。")
    ap.add_argument(
        "--use-ext-as-frequency",
        required=False,
        action="store_true",
        default=False,
        help="使用词库文件中的扩展字段第一个整数作为词频。",
    )
    ap.add_argument(
        "--rime-dir",
        "-d",
        type=str,
        required=False,
        help="Rime 用户文件夹。" "指定后会生成 Rime 词典文件，并放到指定的路径下。",
    )
    return ap.parse_args()


def read_and_save(scel, output, use_ext_as_frequency):
    with open(scel, "rb") as fp:
        hz_offset = get_hz_offset(fp)

        meta = get_dict_meta(fp)
        LOGGER.info("细胞词库元信息：%s", meta)
        output = output or meta.title + ".txt"

        py_map = syllable_table(fp)

        file_size = os.path.getsize(scel)
        records = word_table(fp, file_size, hz_offset, py_map, use_ext_as_frequency)
        with open(output, "w", encoding="utf8", newline="\n") as ofp:
            lines = ["\t".join(record) for record in records]
            ofp.write("\n".join(lines))


def process(args):
    scel_file = args.scel
    output = args.output
    if not os.path.exists(scel_file):
        raise ValueError("文件不存在：{}".format(scel_file))
    use_ext_as_frequency = args.use_ext_as_frequency
    read_and_save(scel_file, output, use_ext_as_frequency)


if __name__ == "__main__":
    process(args())
