import argparse
import csv
from pathlib import Path


PHRASES = [
    {
        "id": "hello",
        "english": "Hello.",
        "glosses": ["HALLO"],
    },
    {
        "id": "deaf_sign_slow",
        "english": "I am deaf. Please sign slowly.",
        "glosses": ["ICH", "GEBAERDEN", "BITTE", "LANGSAM"],
    },
    {
        "id": "not_understand_repeat_slow",
        "english": "I do not understand. Please repeat slowly.",
        "glosses": ["ICH", "NICHT", "VERSTEHEN", "BITTE", "LANGSAM"],
    },
    {
        "id": "need_doctor_appointment",
        "english": "I am sick. I need a doctor appointment.",
        "glosses": ["ICH", "KRANK", "ARZT", "BITTE", "TREFFEN(-hingehen)"],
    },
    {
        "id": "appointment_today",
        "english": "I can meet today.",
        "glosses": ["ICH", "KANN", "HEUTE", "TREFFEN(-hingehen)"],
    },
    {
        "id": "appointment_later",
        "english": "I cannot meet now. Later.",
        "glosses": ["ICH", "KANN-NICHT", "SPAETER"],
    },
    {
        "id": "please_reply_time",
        "english": "Please reply with the time.",
        "glosses": ["BITTE", "ZEIT", "UHR", "BESCHEID(-auf-mich)"],
    },
    {
        "id": "confirm_10_oclock_today",
        "english": "Confirmed: today at 10:00.",
        "glosses": ["OK", "HEUTE", "num:10", "uhr:0"],
    },
    {
        "id": "understood_ok",
        "english": "I understand. OK.",
        "glosses": ["ICH", "VERSTEHEN", "OK"],
    },
    {
        "id": "thank_you",
        "english": "Thank you.",
        "glosses": ["DANKE"],
    },
    {
        "id": "urgent_reply",
        "english": "Please reply soon.",
        "glosses": ["BITTE", "ZEITNAH", "BESCHEID(-auf-mich)"],
    },
]


CONVERSATION_IDS = [
    "hello",
    "deaf_sign_slow",
    "need_doctor_appointment",
    "please_reply_time",
    "confirm_10_oclock_today",
    "understood_ok",
    "thank_you",
]


def to_path_token(gloss_token: str):
    if ":" in gloss_token:
        category, gloss_name = gloss_token.split(":", 1)
        return category, gloss_name
    return "signs", gloss_token


def exists_in_corpus(generated_root: Path, gloss_token: str):
    category, gloss_name = to_path_token(gloss_token)
    candidate = generated_root / category / "trimmed" / f"{gloss_name}.blend"
    return candidate.exists(), candidate


def write_mms_csv(csv_path: Path, glosses, duration=0.8, transition=0.15):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["maingloss", "framestart", "frameend", "duration", "transition"])
        for i, gloss in enumerate(glosses):
            writer.writerow([gloss, 0, 0, duration, 0 if i == 0 else transition])


def build_phrasebook(output_dir: Path):
    phrasebook_path = output_dir / "README.md"
    lines = [
        "# Basic English Hardcoded Phrases (MMS)",
        "",
        "These are hardcoded daily-life phrases mapped to available corpus glosses.",
        "The English text is practical intent, not a word-by-word linguistic translation.",
        "",
        "## Phrases",
        "",
    ]
    for item in PHRASES:
        gloss_list = ", ".join(item["glosses"])
        lines.append(f"- `{item['id']}`: {item['english']}  ")
        lines.append(f"  Glosses: `{gloss_list}`")

    lines.extend(
        [
            "",
            "## Conversation",
            "",
            "Generated file: `conversation_booking_confirmation.mms.csv`",
        ]
    )

    phrasebook_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("MMS-examples") / "basic_english_hardcoded",
        help="Where generated MMS files are written.",
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=None,
        help="Path to corpus generated root for gloss validation.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.8,
        help="Per-gloss duration in seconds.",
    )
    parser.add_argument(
        "--transition",
        type=float,
        default=0.15,
        help="Transition duration between glosses in seconds.",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    missing = []
    if args.generated_root is not None:
        for phrase in PHRASES:
            for gloss in phrase["glosses"]:
                ok, path = exists_in_corpus(args.generated_root, gloss)
                if not ok:
                    missing.append((gloss, str(path)))

    if missing:
        print("Missing gloss clips in corpus:")
        for gloss, path in missing:
            print(f"  - {gloss}: {path}")
        raise SystemExit(1)

    phrase_by_id = {item["id"]: item for item in PHRASES}

    for phrase in PHRASES:
        out_csv = args.output_dir / f"{phrase['id']}.mms.csv"
        write_mms_csv(
            out_csv,
            phrase["glosses"],
            duration=args.duration,
            transition=args.transition,
        )
        print(f"Wrote {out_csv}")

    conversation_glosses = []
    for pid in CONVERSATION_IDS:
        conversation_glosses.extend(phrase_by_id[pid]["glosses"])

    convo_csv = args.output_dir / "conversation_booking_confirmation.mms.csv"
    write_mms_csv(
        convo_csv,
        conversation_glosses,
        duration=args.duration,
        transition=args.transition,
    )
    print(f"Wrote {convo_csv}")

    build_phrasebook(args.output_dir)
    print(f"Wrote {args.output_dir / 'README.md'}")


if __name__ == "__main__":
    main()
