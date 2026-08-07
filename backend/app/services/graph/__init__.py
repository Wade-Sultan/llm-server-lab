"""The LangGraph harness for a chat turn.

`app/services/chat_pipeline.py` still owns every step's actual work — profile
extraction, elicitation, the DSPy build pipeline, the recommendation lead-in.
What lives here is only the shape: which step runs, in what order, and what
carries between them.

The split is deliberate. Those functions are individually testable and are
called from nowhere else; wrapping them in a graph should not have meant
rewriting them, and it didn't.
"""
