import marimo

__generated_with = "0.23.13"
app = marimo.App(css_file="")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md(r"""
    # UI preview — VSCode-style theme

    ## What changed

    This notebook loads `vscode-theme.css`, so what you're seeing is the new look:

    - **Text and headings** render in your system UI font (Segoe UI on Windows) instead of PT Sans and the serif Lora.
    - **Code** renders in Cascadia Code / Consolas — the same fonts VSCode uses.
    - **Headings are semibold**, like VSCode, Jupyter, and GitHub markdown.

    Regular paragraph text looks like this. It should feel crisp and familiar — the
    same reading experience as VSCode's docs pane or Claude's interface, rather than
    an academic paper.
    """)
    return


@app.cell
def _(mo):
    slider = mo.ui.slider(1, 20, value=8, label="Fibonacci terms")
    slider
    return (slider,)


@app.cell
def _(mo, slider):
    def fibonacci(n: int) -> list[int]:
        seq = [0, 1]
        while len(seq) < n:
            seq.append(seq[-1] + seq[-2])
        return seq[:n]

    mo.md(f"`fibonacci({slider.value})` → `{fibonacci(slider.value)}`")
    return


app._unparsable_cell(
    r"""
    pip install pydantic_ai
    """,
    name="_"
)


@app.cell
def _():
    return


@app.cell
def _(mo):
    mo.md(r"""
    Hello! How can I help you with your marimo notebook today?
    """)
    return


if __name__ == "__main__":
    app.run()
