"""子网冲突检测服务

在创建或修改组子网时，检测新子网是否与全局子网或已有组子网存在地址重叠。
"""

from ipaddress import ip_network

from app.utils.cidr import subnets_overlap, validate_cidr, is_subnet_of


def check_subnet_conflict(
    new_subnet: str,
    global_subnet: str,
    existing_groups: list[dict],
    exclude_group_id: str | None = None,
) -> list[str]:
    """检查新子网与全局子网及所有现有组子网的重叠情况。

    检测逻辑：
    1. 新子网格式必须合法
    2. 新子网必须是全局子网的子网（在全局子网范围内）
    3. 新子网不得与任何非根组子网重叠
       - 根组（子网 == 全局子网）不参与重叠检测，因为子组在根组范围内是预期行为

    参数:
        new_subnet:       待检测的子网 CIDR
        global_subnet:    系统全局子网 CIDR
        existing_groups:  所有现有组的字典列表，需含 name / subnet / id 字段
        exclude_group_id: 排除的组 ID（编辑组时排除自身）

    返回:
        所有冲突描述列表，空列表表示无冲突
    """
    conflicts: list[str] = []

    if not validate_cidr(new_subnet):
        conflicts.append(f"子网格式不合法: {new_subnet}")
        return conflicts

    if global_subnet and not is_subnet_of(new_subnet, global_subnet):
        conflicts.append(f"子网 {new_subnet} 不在全局子网 {global_subnet} 范围内")
        return conflicts

    # 预计算全局子网网络对象，用于判断根组
    global_net = None
    if global_subnet:
        try:
            global_net = ip_network(global_subnet, strict=False)
        except (ValueError, TypeError):
            pass

    for group in existing_groups:
        group_id = group.get("id", "")
        if exclude_group_id and group_id == exclude_group_id:
            continue

        group_subnet = group.get("subnet", "")
        if not group_subnet:
            continue

        # 根组（子网 == 全局子网）不参与重叠检测
        # 子组在根组范围内是合法的、预期的
        if global_net:
            try:
                if ip_network(group_subnet, strict=False) == global_net:
                    continue
            except (ValueError, TypeError):
                pass

        if subnets_overlap(new_subnet, group_subnet):
            group_name = group.get("name", group_id)
            conflicts.append(
                f"与组「{group_name}」的子网 {group_subnet} 存在地址重叠"
            )

    return conflicts
