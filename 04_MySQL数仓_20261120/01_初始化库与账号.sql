-- ============================================================================
--  01 · 初始化库与专用账号
--  说明：本文件由 00_一键初始化数据库.ps1 调用，并以 root 身份执行。
--       其中 __APP_PASSWORD__ 会在执行前被脚本替换成随机生成的强密码，
--       同时写入项目根目录的 .env，应用侧只读 .env，不再出现明文密码。
--
--  账号设计（最小权限原则）：
--     bloom_app —— 只对 bloom_oltp / bloom_dw 两个库有全部权限，不是 root
--  这样即使应用被攻破，损失也限制在两个业务库内。
-- ============================================================================

SET NAMES utf8mb4;

-- 两个逻辑库：OLTP 承载交易，DW 承载分析，物理隔离便于按需扩容
CREATE DATABASE IF NOT EXISTS bloom_oltp
    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE DATABASE IF NOT EXISTS bloom_dw
    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- 专用应用账号（localhost 与 127.0.0.1 都建，避免 host 解析差异导致连不上）
CREATE USER IF NOT EXISTS 'bloom_app'@'localhost' IDENTIFIED BY '__APP_PASSWORD__';
CREATE USER IF NOT EXISTS 'bloom_app'@'127.0.0.1' IDENTIFIED BY '__APP_PASSWORD__';
ALTER USER 'bloom_app'@'localhost' IDENTIFIED BY '__APP_PASSWORD__';
ALTER USER 'bloom_app'@'127.0.0.1' IDENTIFIED BY '__APP_PASSWORD__';

GRANT ALL PRIVILEGES ON bloom_oltp.* TO 'bloom_app'@'localhost';
GRANT ALL PRIVILEGES ON bloom_dw.*   TO 'bloom_app'@'localhost';
GRANT ALL PRIVILEGES ON bloom_oltp.* TO 'bloom_app'@'127.0.0.1';
GRANT ALL PRIVILEGES ON bloom_dw.*   TO 'bloom_app'@'127.0.0.1';

FLUSH PRIVILEGES;

SELECT '库与账号初始化完成' AS msg,
       @@version AS mysql_version,
       @@character_set_server AS charset;
