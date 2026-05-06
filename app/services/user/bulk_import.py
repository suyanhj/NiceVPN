"""用户批量导入服务 — CSV/TXT 全量预检式导入"""
import csv
import io
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """预检结果"""
    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    """单个用户导入结果"""
    username: str
    group: str
    success: bool
    message: str = ""


class BulkImportService:
    def parse_file(self, file_bytes: bytes, filename: str) -> list[dict]:
        """
        解析上传的文件。
        - CSV: 逗号分隔，含表头行 username,group
        - TXT: 空格分隔，每行 <用户名> <组名>
        强制 UTF-8 编码。
        返回 [{"username": "...", "group": "..."}, ...] 或抛出 ValueError（格式错误）。
        """
        try:
            content = file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError("文件必须使用 UTF-8 编码")

        rows = []
        if filename.lower().endswith('.csv'):
            # CSV 格式：含表头 username,group
            reader = csv.DictReader(io.StringIO(content))
            if 'username' not in reader.fieldnames or 'group' not in reader.fieldnames:
                raise ValueError("CSV 文件必须包含 username 和 group 列")
            for row in reader:
                if row.get('username') and row.get('group'):
                    rows.append({'username': row['username'].strip(), 'group': row['group'].strip()})
        elif filename.lower().endswith('.txt'):
            # TXT 格式：空格分隔
            for line_num, line in enumerate(content.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) != 2:
                    raise ValueError(f"第 {line_num} 行格式错误，应为：<用户名> <组名>")
                rows.append({'username': parts[0], 'group': parts[1]})
        else:
            raise ValueError("仅支持 .csv 或 .txt 文件")

        if not rows:
            raise ValueError("文件为空或无有效数据")

        return rows

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

        for idx, row in enumerate(rows, 1):
            username = row['username']
            group = row['group']

            # 检查组是否存在
            if group not in existing_groups:
                errors.append(f"第 {idx} 行：组 '{group}' 不存在")

            # 检查用户名是否已存在
            if username in existing_users:
                errors.append(f"第 {idx} 行：用户 '{username}' 已存在")

            # 检查文件内重复
            if username in seen_usernames:
                errors.append(f"第 {idx} 行：用户 '{username}' 在文件中重复")

            seen_usernames.add(username)

        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def import_batch(self, rows: list[dict],
                     group_name_to_id: dict[str, str]) -> list[ImportResult]:
        """
        仅在 validate_all 通过后调用。
        逐一调用 UserService.create 创建用户，收集每行结果。
        """
        from app.services.user.service import UserService

        user_service = UserService()
        results = []

        for row in rows:
            username = row['username']
            group_name = row['group']
            group_id = group_name_to_id[group_name]

            try:
                user_service.create(username=username, group_id=group_id)
                results.append(ImportResult(
                    username=username,
                    group=group_name,
                    success=True,
                    message="创建成功"
                ))
            except Exception as e:
                results.append(ImportResult(
                    username=username,
                    group=group_name,
                    success=False,
                    message=str(e)
                ))

        return results
