from pathlib import Path

from wechat_rpa.people_aliases import PersonAliasResolver


def test_person_alias_resolver_exact_and_wildcard(tmp_path: Path) -> None:
    aliases = tmp_path / "PEOPLE_ALIASES.md"
    aliases.write_text(
        "\n".join(
            [
                "- 刘晓亮 -> 嘴甜心善体育生, 魔法少女",
                "- 统皇 -> *cong, maggie_61",
            ]
        ),
        encoding="utf-8",
    )
    resolver = PersonAliasResolver(str(aliases))

    assert resolver.resolve("嘴甜心善体育生") == "刘晓亮"
    assert resolver.resolve("魔法少女") == "刘晓亮"
    assert resolver.resolve("餮虢cong") == "统皇"
    assert resolver.resolve("maggie_61") == "统皇"
    assert resolver.resolve("123456") == ""


def test_person_alias_resolver_reload_on_change(tmp_path: Path) -> None:
    aliases = tmp_path / "PEOPLE_ALIASES.md"
    aliases.write_text("- 晨哥 -> Gromit\n", encoding="utf-8")
    resolver = PersonAliasResolver(str(aliases))

    assert resolver.resolve("Gromit") == "晨哥"

    aliases.write_text("- 晨哥 -> Gromit\n- 张捷 -> 巴音布鲁克之王\n", encoding="utf-8")
    assert resolver.resolve("巴音布鲁克之王") == "张捷"
