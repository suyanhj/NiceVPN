# 任务

- [x] PeerInstance 新增 mesh_route_visible_tag_ids 和规范化校验
- [x] Mesh 路由可见性改为组/标签 OR 匹配，支持用户直接标签和活动组继承标签
- [x] 一次读取 user_tags/group_tags，避免 Mesh 路由同步 N+1 查询
- [x] 对端页面增加标签多选、编辑回填和可见范围展示
- [x] 停用用户时重算 CCD 并移除 Mesh push route
- [x] 远端链缓存保存逻辑 tag_binding 行
- [x] 对端远端防火墙创建、编辑、启停、删除和推送支持动态标签规则
- [x] 标签成员/用户组状态或地址变化时异步刷新远端规则
- [x] 远端同步失败持久化 pending_sync 和错误信息
- [x] 动态标签规则存在时保护自动拉取，手动拉取前二次确认
- [x] 标签删除保护 peer 路由和远端规则引用
- [x] 删除 peer 时清理本地远端链缓存及 prepush 备份
- [x] 补充 Mesh、远端标签规则、缓存生命周期测试与文档
- [ ] CI 全部通过并完成代码审查
