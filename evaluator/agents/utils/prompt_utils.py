import os

def load_prompt_template(file_name: str) -> str:
    file_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), '..', 'prompts', file_name)
    )
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Prompt template not found: prompts/{file_name}")
    with open(file_path, 'r') as f:
        return f.read()


def load_internal_context_as_prompt(working_dir: str, file_name: str) -> str:
    file_path = os.path.join(working_dir, 'internal_context', file_name)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Internal context file {file_path} not found")
    with open(file_path, 'r') as f:
        return f.read()