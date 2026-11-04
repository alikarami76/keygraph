"""Built-in rules for public ORM conventions. Data only: the same structure as a user's code-patterns.yaml.

Named groups: `table` (a table name), `column` (an explicit column name), `prop` (a property or field name, mapped
to a column through naming variants), `entity` (the class or struct the declaration belongs to). Built-in rules use
scope "entity": a column binds to the table declared by the class, struct or model that contains it. A rule with
`fallback: true` declares a table only when its block declares no other table (a Prisma model without @@map)."""

from __future__ import annotations

_PHP_PROP = r"(?:(?:public|protected|private|readonly|static|var)\s+)+(?:\??[\w\\|]+\s+)?\$(?P<prop>\w+)"
_JAVA_FIELD = r"(?:(?:private|protected|public|final|var|val|lateinit|open|override)\s+)*(?:[\w<>\[\],.?]+\s+)?(?P<prop>\w+)\s*[;:=]"
_TS_PROP = r"(?:(?:public|private|protected|readonly|declare)\s+)*(?P<prop>\w+)[?!]?\s*:"

BUILTIN_RULES: list[dict] = [
    # PHP Doctrine
    {"name": "doctrine-table-attribute", "languages": ["php"], "kind": "declare_table",
     "regex": r"#\[(?:ORM\\)?Table\(\s*(?:name\s*:\s*)?['\"](?P<table>[\w.]+)['\"]"},
    {"name": "doctrine-table-annotation", "languages": ["php"], "kind": "declare_table",
     "regex": r"@(?:ORM\\)?Table\(\s*name\s*=\s*\"(?P<table>[\w.]+)\""},
    {"name": "doctrine-column-attribute", "languages": ["php"], "kind": "declare_column",
     "regex": r"#\[(?:ORM\\)?(?:Column|JoinColumn)(?:\((?:[^)]*?\bname\s*:\s*['\"](?P<column>\w+)['\"])?[^)]*\))?\]\s*"
              r"(?:#\[[^\]]*\]\s*)*" + _PHP_PROP},
    {"name": "doctrine-column-annotation", "languages": ["php"], "kind": "declare_column",
     "regex": r"@(?:ORM\\)?(?:Column|JoinColumn)\((?:[^)]*?\bname\s*=\s*\"(?P<column>\w+)\")?[^)]*\)(?:(?!\*/)[\s\S])*\*/\s*"
              + _PHP_PROP},
    # Python SQLAlchemy
    {"name": "sqlalchemy-tablename", "languages": ["py"], "kind": "declare_table",
     "regex": r"\b__tablename__\s*=\s*['\"](?P<table>[\w.]+)['\"]"},
    {"name": "sqlalchemy-core-table", "languages": ["py"], "kind": "declare_table",
     "regex": r"\bTable\(\s*['\"](?P<table>[\w.]+)['\"]\s*,"},
    {"name": "sqlalchemy-column", "languages": ["py"], "kind": "declare_column",
     "regex": r"^[ \t]*(?P<prop>[A-Za-z_]\w*)\s*(?::[^=\n]+)?=\s*(?:\w+\.)?(?:Column|mapped_column)\(\s*(?:['\"](?P<column>\w+)['\"])?"},
    {"name": "sqlalchemy-core-column", "languages": ["py"], "kind": "declare_column",
     "regex": r"\bColumn\(\s*['\"](?P<column>\w+)['\"]"},
    # Python Django
    {"name": "django-db-table", "languages": ["py"], "kind": "declare_table",
     "regex": r"\bdb_table\s*=\s*['\"](?P<table>[\w.]+)['\"]"},
    {"name": "django-db-column", "languages": ["py"], "kind": "declare_column",
     "regex": r"^[ \t]*(?P<prop>[A-Za-z_]\w*)\s*=\s*models\.\w+\([^\n]*?\bdb_column\s*=\s*['\"](?P<column>\w+)['\"]"},
    {"name": "django-field", "languages": ["py"], "kind": "declare_column",
     "regex": r"^[ \t]*(?P<prop>[A-Za-z_]\w*)\s*=\s*models\.(?!ManyToMany)\w+(?:Field|Key)\((?![^\n]*\bdb_column\b)"},
    # TypeScript / JavaScript TypeORM
    {"name": "typeorm-entity", "languages": ["ts", "js"], "kind": "declare_table",
     "regex": r"@Entity\(\s*['\"](?P<table>[\w.]+)['\"]"},
    {"name": "typeorm-entity-options", "languages": ["ts", "js"], "kind": "declare_table",
     "regex": r"@Entity\(\s*\{[^}]*?\bname\s*:\s*['\"](?P<table>[\w.]+)['\"]"},
    {"name": "typeorm-column", "languages": ["ts", "js"], "kind": "declare_column",
     "regex": r"@(?:Column|PrimaryColumn|PrimaryGeneratedColumn|CreateDateColumn|UpdateDateColumn|DeleteDateColumn|JoinColumn)"
              r"\((?:[^)]*?\bname\s*:\s*['\"](?P<column>\w+)['\"])?[^)]*\)\s*(?:@\w+\([^)]*\)\s*)*" + _TS_PROP},
    # Prisma
    {"name": "prisma-map", "languages": ["prisma"], "kind": "declare_table",
     "regex": r"@@map\(\s*\"(?P<table>[\w.]+)\"\s*\)"},
    {"name": "prisma-model", "languages": ["prisma"], "kind": "declare_table", "fallback": True,
     "regex": r"^model[ \t]+(?P<table>\w+)[ \t]*\{"},
    {"name": "prisma-field-map", "languages": ["prisma"], "kind": "declare_column",
     "regex": r"^[ \t]+(?P<prop>\w+)[ \t]+\w+(?:\[\]|\?)?[^\n]*?@map\(\s*\"(?P<column>\w+)\"\s*\)"},
    {"name": "prisma-field", "languages": ["prisma"], "kind": "declare_column",
     "regex": r"^[ \t]+(?P<prop>[A-Za-z_]\w*)[ \t]+\w+(?:\[\]|\?)?(?![^\n]*@(?:map|relation)\()(?=[ \t]|$)"},
    # Java / Kotlin JPA and Hibernate
    {"name": "jpa-table", "languages": ["java", "kt"], "kind": "declare_table",
     "regex": r"@Table\(\s*(?:[^)]*?\bname\s*=\s*)?\"(?P<table>[\w.]+)\""},
    {"name": "jpa-column", "languages": ["java", "kt"], "kind": "declare_column",
     "regex": r"@(?:Column|JoinColumn)(?:\((?:[^)]*?\bname\s*=\s*\"(?P<column>\w+)\")?[^)]*\))?\s*(?:@\w+(?:\([^)]*\))?\s*)*"
              + _JAVA_FIELD},
    # Go GORM
    {"name": "gorm-tablename", "languages": ["go"], "kind": "declare_table",
     "regex": r"func\s*\(\s*(?:\w+\s+)?\*?(?P<entity>\w+)\s*\)\s*TableName\(\)\s*string\s*\{\s*return\s+\"(?P<table>[\w.]+)\""},
    {"name": "gorm-column-tag", "languages": ["go"], "kind": "declare_column",
     "regex": r"^[ \t]*(?P<prop>\w+)[ \t]+[^\n`]*`[^`\n]*\bgorm:\"[^\"]*?\bcolumn:(?P<column>\w+)"},
    # Ruby on Rails
    {"name": "rails-table-name", "languages": ["rb"], "kind": "declare_table",
     "regex": r"\bself\.table_name\s*=\s*['\"](?P<table>[\w.]+)['\"]"},
]

# class / struct / model headers per language; group 1 is the block name
BLOCK_HEADERS = {
    "php": r"\b(?:class|trait|interface|enum)\s+(\w+)",
    "java": r"\b(?:class|interface|record|enum)\s+(\w+)",
    "kt": r"\b(?:class|interface|object)\s+(\w+)",
    "cs": r"\b(?:class|interface|record|struct)\s+(\w+)",
    "ts": r"\bclass\s+(\w+)",
    "js": r"\bclass\s+(\w+)",
    "go": r"\btype\s+(\w+)\s+struct\b",
    "prisma": r"^(?:model|enum|type|view)[ \t]+(\w+)",
}
INDENT_HEADERS = {"py": r"^([ \t]*)class\s+(\w+)", "rb": r"^([ \t]*)class\s+([\w:]+)"}
