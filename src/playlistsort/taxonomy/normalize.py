"""Map raw genre tags from any source onto the canonical taxonomy in config/taxonomy.yaml.

Resolution order for `normalize(raw, source, taxonomy, source_tag_id=None)`:
  1. a Beatport numeric id (`source_tag_id`) when the source is Beatport;
  2. the "Genre | Sub-genre" delivery form;
  3. an alias rule applicable to the source (aliases may split a vote across several targets,
     or point at a family for broad tags);
  4. an exact genre name (any source) or an exact sub-genre name (Beatport-like sources only,
     because bare sub-genre words such as "Dub" or "Raw" mean other things elsewhere);
  5. otherwise nothing, and the (source, raw) pair is counted in `taxonomy.unmapped`.
Matching is case- and punctuation-insensitive.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

Specificity = Literal["subgenre", "genre", "family"]

# Sources whose bare sub-genre names can be trusted (they use Beatport's vocabulary).
SUBGENRE_NAME_SOURCES = frozenset({"beatport", "sonovault"})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """Lower-case, '&' -> 'and', drop everything that is not a letter or digit."""
    return _NON_ALNUM.sub("", text.lower().replace("&amp;", "&").replace("&", " and "))


@dataclass(frozen=True)
class Canonical:
    tag: str  # "genre_key", "genre_key.subgenre_key" or "family:<family_key>"
    name: str
    genre_key: str | None
    subgenre_key: str | None
    family: str
    specificity: Specificity
    weight: float = 1.0
    via_alias: str | None = None


@dataclass(frozen=True)
class SubgenreNode:
    key: str
    name: str
    beatport_id: int | None = None


@dataclass(frozen=True)
class GenreNode:
    key: str
    name: str
    family: str
    beatport_id: int | None = None
    subgenres: dict[str, SubgenreNode] = field(default_factory=dict)


@dataclass(frozen=True)
class AliasRule:
    raw: str
    targets: tuple[str, ...]
    weights: tuple[float, ...]
    sources: frozenset[str] | None = None
    note: str | None = None

    def applies_to(self, source: str) -> bool:
        return self.sources is None or source in self.sources


class TaxonomyError(ValueError):
    pass


class Taxonomy:
    def __init__(
        self,
        *,
        version: str,
        genres: Sequence[GenreNode],
        families: dict[str, str],
        aliases: Sequence[AliasRule],
    ) -> None:
        self.version = version
        self.genres: dict[str, GenreNode] = {g.key: g for g in genres}
        self.families = dict(families)
        self.aliases = list(aliases)
        self.unmapped: Counter[tuple[str, str]] = Counter()

        self._genre_by_slug: dict[str, str] = {}
        self._sub_by_slug: dict[str, list[str]] = {}
        self._by_beatport_id: dict[str, str] = {}
        for g in genres:
            if g.family not in self.families:
                raise TaxonomyError(f"genre {g.key!r} references unknown family {g.family!r}")
            self._genre_by_slug[slug(g.name)] = g.key
            if g.beatport_id is not None:
                self._by_beatport_id[str(g.beatport_id)] = g.key
            for s in g.subgenres.values():
                tag = f"{g.key}.{s.key}"
                self._sub_by_slug.setdefault(slug(s.name), []).append(tag)
                if s.beatport_id is not None:
                    self._by_beatport_id[str(s.beatport_id)] = tag
        self._alias_by_slug: dict[str, list[AliasRule]] = {}
        for rule in aliases:
            for target in rule.targets:
                self._validate_target(target, rule.raw)
            self._alias_by_slug.setdefault(slug(rule.raw), []).append(rule)

    # ------------------------------------------------------------------ lookups

    def _validate_target(self, target: str, raw: str) -> None:
        if target.startswith("family:"):
            if target[7:] not in self.families:
                raise TaxonomyError(f"alias {raw!r} -> unknown family {target!r}")
            return
        if target not in self.tags():
            raise TaxonomyError(f"alias {raw!r} -> unknown tag {target!r}")

    def tags(self) -> list[str]:
        out: list[str] = []
        for g in self.genres.values():
            out.append(g.key)
            out.extend(f"{g.key}.{s}" for s in g.subgenres)
        return out

    def _split(self, tag: str) -> tuple[GenreNode, SubgenreNode | None]:
        genre_key, _, sub_key = tag.partition(".")
        try:
            g = self.genres[genre_key]
        except KeyError as exc:
            raise TaxonomyError(f"unknown tag {tag!r}") from exc
        if not sub_key:
            return g, None
        try:
            return g, g.subgenres[sub_key]
        except KeyError as exc:
            raise TaxonomyError(f"unknown tag {tag!r}") from exc

    def display_name(self, tag: str) -> str:
        if tag.startswith("family:"):
            return self.families[tag[7:]]
        g, s = self._split(tag)
        return s.name if s else g.name

    def path_name(self, tag: str, sep: str = " › ") -> str:
        if tag.startswith("family:"):
            return self.families[tag[7:]]
        g, s = self._split(tag)
        return f"{g.name}{sep}{s.name}" if s else g.name

    def canonical(
        self, target: str, *, weight: float = 1.0, via_alias: str | None = None
    ) -> Canonical:
        if target.startswith("family:"):
            fam = target[7:]
            return Canonical(
                tag=target,
                name=self.families[fam],
                genre_key=None,
                subgenre_key=None,
                family=fam,
                specificity="family",
                weight=weight,
                via_alias=via_alias,
            )
        g, s = self._split(target)
        return Canonical(
            tag=target,
            name=s.name if s else g.name,
            genre_key=g.key,
            subgenre_key=s.key if s else None,
            family=g.family,
            specificity="subgenre" if s else "genre",
            weight=weight,
            via_alias=via_alias,
        )


# ---------------------------------------------------------------------- loading


def load_taxonomy(path: str | Path) -> Taxonomy:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    genres: list[GenreNode] = []
    for g in data["genres"]:
        subs = {
            s["key"]: SubgenreNode(key=s["key"], name=s["name"], beatport_id=s.get("beatport_id"))
            for s in g.get("subgenres", []) or []
        }
        genres.append(
            GenreNode(
                key=g["key"],
                name=g["name"],
                family=g["family"],
                beatport_id=g.get("beatport_id"),
                subgenres=subs,
            )
        )
    aliases: list[AliasRule] = []
    for a in data.get("aliases", []) or []:
        targets = tuple(a["targets"]) if "targets" in a else (a["target"],)
        if "weights" in a:
            weights = tuple(float(w) for w in a["weights"])
            if len(weights) != len(targets):
                raise TaxonomyError(f"alias {a['raw']!r}: weights/targets length mismatch")
        else:
            weights = tuple([1.0 / len(targets)] * len(targets))
        sources = frozenset(a["sources"]) if a.get("sources") else None
        aliases.append(
            AliasRule(
                raw=a["raw"], targets=targets, weights=weights, sources=sources, note=a.get("note")
            )
        )
    return Taxonomy(
        version=str(data.get("version", "")),
        genres=genres,
        families=dict(data.get("families", {})),
        aliases=aliases,
    )


# ---------------------------------------------------------------------- normalising


def normalize(
    raw: str,
    source: str,
    taxonomy: Taxonomy,
    *,
    source_tag_id: str | int | None = None,
) -> tuple[Canonical, ...]:
    text = (raw or "").strip()
    if not text and source_tag_id is None:
        return ()

    # 1. Beatport numeric ids are the most stable identifier we have.
    if source == "beatport" and source_tag_id is not None:
        tag = taxonomy._by_beatport_id.get(str(source_tag_id))
        if tag:
            return (taxonomy.canonical(tag),)
    if not text:
        return ()

    # 2. "Genre | Sub-genre" delivery form.
    if "|" in text:
        left, _, right = text.partition("|")
        genre_key = taxonomy._genre_by_slug.get(slug(left))
        if genre_key:
            for sub_key, sub in taxonomy.genres[genre_key].subgenres.items():
                if slug(sub.name) == slug(right):
                    return (taxonomy.canonical(f"{genre_key}.{sub_key}"),)
            return (taxonomy.canonical(genre_key),)

    key = slug(text)
    if not key:
        return ()

    # 3. Aliases (first applicable rule wins).
    for rule in taxonomy._alias_by_slug.get(key, ()):
        if rule.applies_to(source):
            return tuple(
                taxonomy.canonical(t, weight=w, via_alias=rule.raw)
                for t, w in zip(rule.targets, rule.weights, strict=True)
            )

    # 4. Exact names.
    genre_key = taxonomy._genre_by_slug.get(key)
    if genre_key:
        return (taxonomy.canonical(genre_key),)
    if source in SUBGENRE_NAME_SOURCES:
        subs = taxonomy._sub_by_slug.get(key, [])
        if len(subs) == 1:
            return (taxonomy.canonical(subs[0]),)

    # 5. Unknown.
    taxonomy.unmapped[(source, text.lower())] += 1
    return ()
