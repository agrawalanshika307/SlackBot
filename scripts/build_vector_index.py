"""
scripts/build_vector_index.py
────────────────────────────
A small helper script to index local text files into the Chroma vector store.

Run from the project root:
    python scripts/build_vector_index.py README.md --id project-readme
"""

import argparse
from pathlib import Path

from app.llm.retriever import Retriever


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Index a text file into the AI Teammate vector store."
    )
    parser.add_argument("paths", nargs="+", help="One or more files to index.")
    parser.add_argument(
        "--id",
        dest="document_id",
        required=True,
        help="Document identifier used for chunk IDs.",
    )
    parser.add_argument(
        "--source",
        default="local-file",
        help="Optional source label stored as metadata.",
    )

    args = parser.parse_args()
    retriever = Retriever()
    total_chunks = 0

    for path_str in args.paths:
        path = Path(path_str)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        text = path.read_text(encoding="utf-8")
        total_chunks += retriever.index_text(
            document_id=f"{args.document_id}:{path.name}",
            text=text,
            metadata={"source": args.source, "path": str(path)},
        )

    print(f"Indexed {len(args.paths)} files into the vector store.")
    print(f"Total chunks added: {total_chunks}")


if __name__ == "__main__":
    main()
