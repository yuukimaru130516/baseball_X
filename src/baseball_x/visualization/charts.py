"""グラフ画像生成（matplotlib）。

出力サイズ: 1200×675px（Xのカード推奨サイズ）
"""
import importlib.util
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
from loguru import logger
from matplotlib import font_manager

matplotlib.use("Agg")  # GUIなし環境でも動作させる


def _setup_japanese_font() -> None:
    """日本語フォントを matplotlib に登録する。

    japanize_matplotlib は Python 3.12+ で削除された distutils に依存しており
    import すると失敗するため、パッケージ同梱の IPAexGothic フォント（ipaexg.ttf）を
    直接登録する。システムに日本語フォントが無い環境でも文字化けを防げる。
    """
    spec = importlib.util.find_spec("japanize_matplotlib")
    if spec is None or not spec.origin:
        logger.warning("japanize_matplotlib が見つからず日本語フォントを登録できません")
        return
    font_path = Path(spec.origin).parent / "fonts" / "ipaexg.ttf"
    if not font_path.exists():
        logger.warning(f"日本語フォントが見つかりません: {font_path}")
        return
    font_manager.fontManager.addfont(str(font_path))
    font_name = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams["font.family"] = font_name
    plt.rcParams["axes.unicode_minus"] = False  # マイナス記号の文字化け回避


_setup_japanese_font()

DPI = 100
WIDTH_PX, HEIGHT_PX = 1200, 675
FIGSIZE = (WIDTH_PX / DPI, HEIGHT_PX / DPI)

TEAM_COLORS: dict[str, str] = {
    # npbscholar の team_short（球団ニックネーム）に合わせる
    "ジャイアンツ": "#FF8000",
    "タイガース": "#FFE200",
    "カープ": "#E50012",
    "ドラゴンズ": "#002569",
    "スワローズ": "#00A0E9",
    "ベイスターズ": "#004B96",
    "ホークス": "#F6AA00",
    "ファイターズ": "#073C8A",
    "マリーンズ": "#000000",
    "ライオンズ": "#1B62B5",
    "イーグルス": "#880011",
    "ゴールデンイーグルス": "#880011",
    "バファローズ": "#A09367",
}
DEFAULT_COLOR = "#4477AA"


def _team_color(team: str) -> str:
    return TEAM_COLORS.get(team, DEFAULT_COLOR)


def ranking_bar(
    df: pd.DataFrame,
    metric_col: str,
    metric_label: str,
    title: str,
    output_path: str | Path,
    top_n: int = 10,
    ascending: bool = False,
) -> Path:
    """指標別トップN選手の横棒グラフを生成して保存する。

    Args:
        df: name, team, {metric_col} の列を持つDataFrame。
        metric_col: 表示する指標のカラム名。
        metric_label: グラフ軸のラベル。
        title: グラフタイトル。
        output_path: 保存先パス（PNG）。
        top_n: 表示する選手数。
        ascending: True なら下位N名（Whiff%/Chase% などコンタクト系指標で使用）。

    Returns:
        保存されたファイルのPath。
    """
    cleaned = df.dropna(subset=[metric_col])
    selected = cleaned.nsmallest(top_n, metric_col) if ascending else cleaned.nlargest(top_n, metric_col)
    data = selected.iloc[::-1]
    colors = [_team_color(t) for t in data["team"]]
    labels = [f"{n}（{t}）" for n, t in zip(data["name"], data["team"])]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    bars = ax.barh(labels, data[metric_col], color=colors, edgecolor="white", linewidth=0.5)

    # 値ラベル
    for bar, val in zip(bars, data[metric_col]):
        ax.text(
            bar.get_width() + ax.get_xlim()[1] * 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"{val:.3f}" if isinstance(val, float) else str(val),
            va="center",
            fontsize=10,
        )

    ax.set_xlabel(metric_label, fontsize=12)
    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout(pad=1.5)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def percentile_profile(
    rows: list[dict],
    team: str,
    title: str,
    output_path: str | Path,
) -> Path:
    """単一選手の指標別パーセンタイル（リーグ内順位帯）を横棒で可視化する。

    Args:
        rows: 上から表示したい順に並べた指標の辞書リスト。各要素は
            {"label": 指標名, "percentile": 0〜100, "value_text": 値の表示文字列}。
        team: 選手の所属（team_short）。バー色に使用。
        title: グラフタイトル（選手名・所属を含めると良い）。

    Returns:
        保存されたファイルのPath。
    """
    if not rows:
        raise ValueError("percentile_profile に渡す rows が空です")

    # 上から読めるよう表示順を反転（barh は下から積むため）
    items = list(reversed(rows))
    labels = [r["label"] for r in items]
    pcts = [r["percentile"] for r in items]
    base = _team_color(team)

    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.barh(labels, [100] * len(items), color="#EEEEEE", zorder=1)  # 背景（母集団）
    bars = ax.barh(labels, pcts, color=base, edgecolor="white", linewidth=0.5, zorder=2)

    for bar, r in zip(bars, items):
        ax.text(
            min(bar.get_width() + 2, 100),
            bar.get_y() + bar.get_height() / 2,
            f"{r['value_text']}（上位{100 - r['percentile']}%）",
            va="center",
            fontsize=10,
            zorder=3,
        )

    ax.set_xlim(0, 118)
    ax.set_xlabel("リーグ内パーセンタイル（規定到達者内・右ほど優秀）", fontsize=11)
    ax.axvline(50, color="#999999", linestyle="--", linewidth=0.8, zorder=1)  # 平均ライン
    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout(pad=1.5)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def scatter_two_metrics(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    x_label: str,
    y_label: str,
    title: str,
    output_path: str | Path,
    annotate_top_n: int = 5,
) -> Path:
    """2指標の散布図を生成して保存する。

    Args:
        df: name, team, {x_col}, {y_col} の列を持つDataFrame。
        annotate_top_n: y_col上位N名の選手名を注釈表示する。
    """
    data = df.dropna(subset=[x_col, y_col])
    colors = [_team_color(t) for t in data["team"]]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.scatter(data[x_col], data[y_col], c=colors, alpha=0.7, s=60, edgecolors="white", linewidth=0.5)

    # 上位N名に名前を注釈
    top = data.nlargest(annotate_top_n, y_col)
    for _, row in top.iterrows():
        ax.annotate(
            row["name"],
            (row[x_col], row[y_col]),
            textcoords="offset points",
            xytext=(6, 3),
            fontsize=9,
        )

    ax.set_xlabel(x_label, fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout(pad=1.5)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out
