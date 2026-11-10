# URL 抓取拒绝组播地址

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/utils/url_fetcher.py

## 问题

URL 导入在连接前用 `is_blocked_address` 拒绝非公网地址。该函数只把 `not address.is_global` 和少量云元数据地址视为封锁。Python 3.12.3 与 API 镜像中的 3.13.18 上，`224.0.0.1` 与 `ff02::1` 的 `is_global` 和 `is_multicast` 同时为真，组播因此被放行。`getaddrinfo` 会返回 `::ffff:224.0.0.1`，建连使用该字面量；3.12.3 上它的 `is_multicast` 为假且 `is_global` 为真，只检查 `is_multicast` 仍会放行。

## 决策

### 实现方案

`is_blocked_address` 仍是解析结果进入建连前的唯一地址判定。IPv4-mapped 地址按映射的 IPv4 地址应用全部规则，包括组播、`is_global` 与显式云元数据封锁集合，避免不同 Python 版本对映射地址分类不同。公网单播继续放行，包括映射形式的公网单播。不另加 6to4 或其他地址族规则：它们不是这条组播绕过，建连路径也没有证明需要新的封锁。

拒绝后的错误仍是「禁止访问私网地址」，调用方不建立 socket。

## 替代方案

- 只拒绝 `224.0.0.0/4`：漏掉 IPv6 组播，也漏掉映射组播。
- 把所有 `is_reserved` 或已弃用网段都封锁：会改变当前仍被 `is_global` 放行的公网单播，超出这条缺陷。
- 依赖操作系统拒绝组播 TCP：地址已经通过校验并进入建连参数，防护不在连接前闭合。

## 后果

解析结果里的组播地址和 IPv4-mapped 组播地址不会被连接。普通公网单播，包括 `::ffff:` 形式的公网单播，仍可连接。错误文案仍沿用私网拒绝，不单独区分组播。

## 验证

`backend/test/unit/knowledge/test_url_fetcher.py` 断言组播、映射私网和元数据地址被拒绝，公网单播与 `::ffff:8.8.8.8` 放行；模拟 Python 3.12 将映射 IPv6 地址标为公网时，元数据地址仍不会产生建连调用。
