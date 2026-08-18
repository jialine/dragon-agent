-- 小说实体生命周期管理数据库 schema
-- 核心思想：每个实体（人物/物品/势力/地点/数值/关系）都是数据库记录，
-- 带生命周期状态机，随小说进程演进。生成时查库注入硬约束，生成后回写。

-- ============ 实体总表 ============
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,               -- person/object/faction/place/value/relation
    name TEXT NOT NULL,
    UNIQUE(type, name)
);

-- ============ 人物生命周期 ============
-- status 状态机: 未登场(0) → 活跃(1) → 暂离(2) → 死亡(3)
-- 硬约束: 死亡不可逆(除非复活设定)；活跃/暂离者不可被当陌生人；location 必须与场景一致
CREATE TABLE IF NOT EXISTS persons (
    entity_id INTEGER PRIMARY KEY REFERENCES entities(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '未登场',   -- 未登场/活跃/暂离/死亡
    gender TEXT DEFAULT '',
    age INTEGER,                              -- 年龄（权威值，不许前后不一致）
    title TEXT DEFAULT '',                    -- 当前官职/身份
    location TEXT DEFAULT '',                 -- 当前所在地
    faction TEXT DEFAULT '',                  -- 所属势力
    relation_to_protagonist TEXT DEFAULT '',  -- 与主角关系
    first_appear INTEGER,                     -- 首次登场章节
    last_seen INTEGER,                        -- 最近活跃章节
    death_chapter INTEGER,                    -- 死亡章节（有值=已死）
    traits TEXT DEFAULT '',                   -- 性格/特征（防性格突变）
    notes TEXT DEFAULT ''
);

-- ============ 物品生命周期 ============
-- status 状态机: 未出现(0) → 被持有(1) → 丢失(2)/损毁(3)
CREATE TABLE IF NOT EXISTS objects (
    entity_id INTEGER PRIMARY KEY REFERENCES entities(id),
    name TEXT NOT NULL,
    owner TEXT DEFAULT '',              -- 当前持有者
    status TEXT NOT NULL DEFAULT '未出现',
    acquired_chapter INTEGER,           -- 获得章节
    lost_chapter INTEGER,               -- 失去章节
    notes TEXT DEFAULT ''
);

-- ============ 势力生命周期 ============
-- status 状态机: 兴起(0) → 壮大(1) → 溃散(2)/灭亡(3)
CREATE TABLE IF NOT EXISTS factions (
    entity_id INTEGER PRIMARY KEY REFERENCES entities(id),
    name TEXT NOT NULL,
    leader TEXT DEFAULT '',
    strength INTEGER,                   -- 兵力数（权威值）
    location TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT '兴起',
    established_chapter INTEGER,
    dissolved_chapter INTEGER,
    notes TEXT DEFAULT ''
);

-- ============ 地点 ============
CREATE TABLE IF NOT EXISTS places (
    entity_id INTEGER PRIMARY KEY REFERENCES entities(id),
    name TEXT NOT NULL,
    region TEXT DEFAULT '',             -- 所属州郡
    status TEXT NOT NULL DEFAULT '存在',
    notes TEXT DEFAULT ''
);

-- ============ 数值追踪（认知值、兵力等核心数值） ============
-- 记录当前值 + 变化规则 + 历史，防止数值乱跳
CREATE TABLE IF NOT EXISTS value_trackers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,          -- 如 '认知值' '乡勇团兵力'
    current_value INTEGER,
    unit TEXT DEFAULT '',
    rule TEXT DEFAULT '',               -- 变化规则描述
    updated_chapter INTEGER
);

-- ============ 数值历史（每次变化记录，可回溯） ============
CREATE TABLE IF NOT EXISTS value_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_name TEXT NOT NULL,
    chapter INTEGER,
    old_value INTEGER,
    new_value INTEGER,
    reason TEXT DEFAULT ''
);

-- ============ 人物关系 ============
-- 关系是矛盾高发区（结义/师徒/敌对/主从），必须独立追踪
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    relation TEXT NOT NULL,             -- 结义/师徒/敌对/主从/相识...
    established_chapter INTEGER,
    status TEXT NOT NULL DEFAULT '存续', -- 存续/破裂
    notes TEXT DEFAULT ''
);

-- ============ 事件日志（防剧情重复） ============
-- 记录已发生的重大事件，生成时注入"最近事件"防止重复描写
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter INTEGER,
    summary TEXT NOT NULL,
    entity_names TEXT DEFAULT ''        -- 涉及实体（逗号分隔）
);

-- ============ 时间线 ============
-- 每章的日期 + 主场景地点，防时间线乱
CREATE TABLE IF NOT EXISTS timeline (
    chapter INTEGER PRIMARY KEY,
    date TEXT DEFAULT '',               -- 如 '中平元年三月'
    location TEXT DEFAULT ''            -- 主场景
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_persons_status ON persons(status);
CREATE INDEX IF NOT EXISTS idx_persons_location ON persons(location);
CREATE INDEX IF NOT EXISTS idx_events_chapter ON events(chapter);
CREATE INDEX IF NOT EXISTS idx_relations_ab ON relations(entity_a, entity_b);
