"""CIDR 校验工具单元测试"""
import pytest
from app.utils.cidr import validate_cidr, is_subnet_of, subnets_overlap


class TestValidateCidr:
    """测试 CIDR 格式校验"""

    def test_valid_cidr(self):
        """测试合法的 CIDR 格式"""
        assert validate_cidr("10.8.0.0/16") is True
        assert validate_cidr("192.168.1.0/24") is True
        assert validate_cidr("172.16.0.0/12") is True
        assert validate_cidr("10.0.0.0/8") is True
        # strict=False 允许主机位非零
        assert validate_cidr("10.8.0.1/24") is True

    def test_invalid_cidr(self):
        """测试非法的 CIDR 格式"""
        assert validate_cidr("abc") is False
        assert validate_cidr("999.0.0.0/8") is False
        assert validate_cidr("") is False
        assert validate_cidr("10.8.0.0") is False  # 缺少前缀长度
        assert validate_cidr("10.8.0.0/33") is False  # 前缀长度超出范围
        assert validate_cidr("10.8.0.0/-1") is False  # 负数前缀

    def test_ipv6_rejected(self):
        """测试 IPv6 地址应被拒绝"""
        assert validate_cidr("::1/128") is False
        assert validate_cidr("2001:db8::/32") is False
        assert validate_cidr("fe80::/10") is False


class TestIsSubnetOf:
    """测试子网判断"""

    def test_true_cases(self):
        """测试子网关系为真的情况"""
        # 10.8.1.0/24 是 10.8.0.0/16 的子网
        assert is_subnet_of("10.8.1.0/24", "10.8.0.0/16") is True
        assert is_subnet_of("192.168.1.0/25", "192.168.1.0/24") is True
        assert is_subnet_of("172.16.5.0/24", "172.16.0.0/12") is True

    def test_false_cases(self):
        """测试子网关系为假的情况"""
        # 192.168.1.0/24 不是 10.8.0.0/16 的子网
        assert is_subnet_of("192.168.1.0/24", "10.8.0.0/16") is False
        assert is_subnet_of("10.9.0.0/24", "10.8.0.0/16") is False
        assert is_subnet_of("10.8.0.0/16", "10.8.1.0/24") is False  # 父网段不是子网段的子网

    def test_same_network(self):
        """测试相同网段"""
        # 相同网段应返回 True（子网关系包含自身）
        assert is_subnet_of("10.8.0.0/16", "10.8.0.0/16") is True
        assert is_subnet_of("192.168.1.0/24", "192.168.1.0/24") is True


class TestSubnetsOverlap:
    """测试子网重叠检测"""

    def test_overlap(self):
        """测试存在重叠的情况"""
        # 10.8.0.0/16 与 10.8.1.0/24 重叠
        assert subnets_overlap("10.8.0.0/16", "10.8.1.0/24") is True
        assert subnets_overlap("10.8.1.0/24", "10.8.0.0/16") is True
        assert subnets_overlap("192.168.1.0/24", "192.168.1.128/25") is True

    def test_no_overlap(self):
        """测试不重叠的情况"""
        # 10.8.0.0/24 与 10.9.0.0/24 不重叠
        assert subnets_overlap("10.8.0.0/24", "10.9.0.0/24") is False
        assert subnets_overlap("192.168.1.0/24", "192.168.2.0/24") is False
        assert subnets_overlap("172.16.0.0/16", "172.17.0.0/16") is False

    def test_identical(self):
        """测试完全相同的网段"""
        # 完全相同的网段应视为重叠
        assert subnets_overlap("10.8.0.0/16", "10.8.0.0/16") is True
        assert subnets_overlap("192.168.1.0/24", "192.168.1.0/24") is True
