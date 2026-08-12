"""用户批量导入服务 — CSV/TXT 全量预检式导入"""
import csv
import io
import logging
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """预检结果"""
    valid: bool
    errors: list[str] = field(default_factory=list)


class BulkImportService:
    MAX_QUANTITY = 500

    def parse_file(self, file_bytes: bytes, filename: str) -> list[dict]:
        """
        解析上传的文件。
        - CSV: 逗号分隔，可含表头行 username,group,quantity
        - TXT: 空格分隔，每行 <用户名> <组名> [数量]
        强制 UTF-8 编码。
        返回 [{"username": "...", "group": "..."}, ...] 或抛出 ValueError（格式错误）。
        """
        try:
            content = file_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            logger.error("批量导入文件不是 UTF-8 编码 file=%s", filename)
            raise ValueError("文件必须使用 UTF-8 编码") from exc

        lowered_filename = filename.lower()
        if not lowered_filename.endswith((".csv", ".txt", ".text")):
            raise ValueError("仅支持 .csv 或 .txt 文件")
        if lowered_filename.endswith(".csv"):
            first_line = next(
                (
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ),
                "",
            )
            header = [
                part.strip()
                for part in next(csv.reader(io.StringIO(first_line)), [])
            ]
            if not self._is_header(header):
                raise ValueError(
                    "CSV 文件必须包含 username、group 和可选 quantity 列"
                )
        return self.parse_text(content)

    def parse_text(self, content: str) -> list[dict]:
        """解析并展开批量导入文本。

        Args:
            content: UTF-8 解码后的 CSV/TXT 文本，每行格式为
                ``username group [quantity]`` 或
                ``username,group[,quantity]``。

        Returns:
            展开后的用户记录；数量大于 1 时依次生成基础用户名和
            ``_1`` 到 ``_(quantity-1)``。

        Raises:
            ValueError: 内容为空、字段数量错误或数量不在 1 到 500 之间。
        """
        rows: list[dict] = []
        for line_num, raw_line in enumerate(content.splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if "," in line:
                parts = [part.strip() for part in next(csv.reader(io.StringIO(line)))]
            else:
                parts = line.split()

            if not rows and self._is_header(parts):
                continue
            if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
                raise ValueError(
                    f"第 {line_num} 行格式错误，应为：<用户名> <组名> [数量]"
                )

            quantity = self._parse_quantity(parts[2] if len(parts) == 3 else "1", line_num)
            username, group = parts[0], parts[1]
            rows.append({"username": username, "group": group})
            rows.extend(
                {"username": f"{username}_{index}", "group": group}
                for index in range(1, quantity)
            )

        if not rows:
            raise ValueError("文件为空或无有效数据")

        return rows

    @staticmethod
    def _is_header(parts: list[str]) -> bool:
        """判断当前字段是否为支持的英文表头。"""
        if len(parts) not in (2, 3):
            return False
        lowered = [part.lower() for part in parts]
        return lowered[:2] == ["username", "group"] and (
            len(lowered) == 2 or lowered[2] in {"quantity", "count"}
        )

    def _parse_quantity(self, raw_quantity: str, line_num: int) -> int:
        """解析单行数量并执行范围校验。"""
        try:
            quantity = int(raw_quantity)
        except ValueError as exc:
            raise ValueError(
                f"第 {line_num} 行数量必须是 1 到 {self.MAX_QUANTITY} 的整数"
            ) from exc
        if not 1 <= quantity <= self.MAX_QUANTITY:
            raise ValueError(
                f"第 {line_num} 行数量必须是 1 到 {self.MAX_QUANTITY} 的整数"
            )
        return quantity

    def validate_all(self, rows: list[dict],
                     existing_groups: dict[str, str],
                     existing_users: set[str]) -> ValidationResult:
        """
        全量预检：
        1. 所有涉及的组名在 existing_groups 中均已存在
        2. 所有用户名在 existing_users 中均不重复
        3. 文件内用户名无重复
        返回 ValidationResult（全通过或全拒绝，无中间态）。
        existing_groups: {group_name: group_id}
        existing_users: set of existing usernames
        """
        errors = []
        seen_usernames = set()

        # rows 是数量展开后的结果，序号与源文件行号不对应，故按用户名定位问题
        for row in rows:
            username = row['username']
            group = row['group']

            # 检查组是否存在
            if group not in existing_groups:
                errors.append(f"用户 '{username}' 引用的组 '{group}' 不存在")

            # 检查用户名是否已存在
            if username in existing_users:
                errors.append(f"用户 '{username}' 已存在")

            # 检查导入内容内重复（含数量展开后产生的同名）
            if username in seen_usernames:
                errors.append(f"用户 '{username}' 在导入内容中重复")

            seen_usernames.add(username)

        return ValidationResult(valid=len(errors) == 0, errors=errors)
