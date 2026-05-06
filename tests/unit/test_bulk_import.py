"""批量导入预检逻辑单元测试"""
import pytest
from app.services.user.bulk_import import BulkImportService


@pytest.fixture
def service():
    """创建 BulkImportService 实例"""
    return BulkImportService()


class TestParseFile:
    """测试文件解析逻辑"""

    def test_csv_valid(self, service):
        """测试合法的 CSV 文件（含表头 username,group）"""
        csv_content = "username,group\nalice,dev\nbob,ops\n"
        rows = service.parse_file(csv_content.encode("utf-8"), "users.csv")
        assert len(rows) == 2
        assert rows[0] == {"username": "alice", "group": "dev"}
        assert rows[1] == {"username": "bob", "group": "ops"}

    def test_txt_valid(self, service):
        """测试合法的空格分隔 TXT 文件"""
        txt_content = "alice dev\nbob ops\n"
        rows = service.parse_file(txt_content.encode("utf-8"), "users.txt")
        assert len(rows) == 2
        assert rows[0] == {"username": "alice", "group": "dev"}
        assert rows[1] == {"username": "bob", "group": "ops"}

    def test_invalid_encoding(self, service):
        """测试非 UTF-8 编码应抛出 ValueError"""
        # 使用 GBK 编码的中文内容
        gbk_bytes = "用户名,组\n张三,开发组\n".encode("gbk")
        with pytest.raises(ValueError, match="UTF-8"):
            service.parse_file(gbk_bytes, "users.csv")

    def test_wrong_columns(self, service):
        """测试 CSV 列名不正确应抛出 ValueError"""
        csv_content = "name,team\nalice,dev\n"
        with pytest.raises(ValueError, match="username"):
            service.parse_file(csv_content.encode("utf-8"), "users.csv")

    def test_empty_file(self, service):
        """测试空文件应抛出 ValueError"""
        # CSV 只有表头，无数据行
        csv_content = "username,group\n"
        with pytest.raises(ValueError, match="空"):
            service.parse_file(csv_content.encode("utf-8"), "users.csv")

        # 完全空的 TXT 文件
        with pytest.raises(ValueError, match="空"):
            service.parse_file(b"", "users.txt")


class TestValidateAll:
    """测试全量预检逻辑"""

    @pytest.fixture
    def existing_groups(self):
        """模拟已存在的组"""
        return {"dev": "grp-001", "ops": "grp-002"}

    @pytest.fixture
    def existing_users(self):
        """模拟已存在的用户"""
        return {"admin", "root"}

    def test_all_valid(self, service, existing_groups, existing_users):
        """测试全部通过的情况"""
        rows = [
            {"username": "alice", "group": "dev"},
            {"username": "bob", "group": "ops"},
        ]
        result = service.validate_all(rows, existing_groups, existing_users)
        assert result.valid is True
        assert result.errors == []

    def test_missing_group(self, service, existing_groups, existing_users):
        """测试引用不存在的组，整批拒绝"""
        rows = [
            {"username": "alice", "group": "dev"},
            {"username": "bob", "group": "nonexistent"},
        ]
        result = service.validate_all(rows, existing_groups, existing_users)
        assert result.valid is False
        assert any("不存在" in e for e in result.errors)

    def test_duplicate_username(self, service, existing_groups, existing_users):
        """测试与已有用户重名，整批拒绝"""
        rows = [
            {"username": "admin", "group": "dev"},  # admin 已存在
            {"username": "alice", "group": "ops"},
        ]
        result = service.validate_all(rows, existing_groups, existing_users)
        assert result.valid is False
        assert any("已存在" in e for e in result.errors)

    def test_internal_duplicate(self, service, existing_groups, existing_users):
        """测试文件内用户名重复，整批拒绝"""
        rows = [
            {"username": "alice", "group": "dev"},
            {"username": "alice", "group": "ops"},  # 文件内重复
        ]
        result = service.validate_all(rows, existing_groups, existing_users)
        assert result.valid is False
        assert any("重复" in e for e in result.errors)
