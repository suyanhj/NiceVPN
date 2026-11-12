# 任务

- [x] 增加对端标签 ipset 内容寻址命名和 peer 隔离
- [x] 增加远端 ipset 安装检测与 Debian/RHEL 自动安装
- [x] 远端动态标签规则按一个标签一个 hash:net 集合生成 set matcher
- [x] 使用 staging/swap 更新集合，iptables-restore 原子切换用户链
- [x] 成功后按 peer 命名空间 GC 孤儿集合，失败清理本轮新建集合
- [x] 原始规则引用现有 NiceVPN 集合时避免误 GC
- [x] 删除 peer 时清理远端标签集合
- [x] 用户/组成员关联纳入标签删除保护，服务层与 SQLite 层双重校验
- [x] 标签列表删除按钮根据所有成员和引用禁用
- [x] 增加远端 ipset、原子应用、GC 和标签删除测试
- [x] 更新 firewall-tags 文档与 UI 提示
- [ ] Python 3.14、Docker、浏览器 smoke、OpenSpec CI 全部通过
