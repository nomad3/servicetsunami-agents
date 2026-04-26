# script.py
import sys
import json


def execute(inputs):
    user_name = inputs.get("user_name", "World")
    return {"greeting": f"Hello, {user_name}!"}


if __name__ == "__main__":
    # This part is for standalone execution and testing
    input_str = sys.stdin.read()
    inputs = json.loads(input_str)
    result = execute(inputs)
    print(json.dumps(result))
