
import builtins
import inspect

def hook_print():
    def custom_print(*args, **kwargs):
        # Add your custom behavior here
        # For example, prepend 'Custom Message: ' to all print statements
        stack = inspect.stack()
        caller_info = stack[1]  # This is a FrameInfo object

        # Extracting relevant information about the caller
        caller_frame = caller_info.frame
        caller_module = inspect.getmodule(caller_frame)
        module_name = caller_module.__name__ if caller_module else "<unknown>"
        file_name = caller_info.filename
        line_number = caller_info.lineno
        function_name = caller_info.function
        builtins._print(f"[PRINT] Called from {module_name} in {function_name} at {file_name}:{line_number}:", *args, **kwargs)

    # Save the original print function
    builtins._print = builtins.print

    # Override the built-in print
    builtins.print = custom_print

def hook_input():
    def custom_input(*args, **kwargs):
        # Add your custom behavior here
        # For example, prepend 'Custom Message: ' to all print statements
        stack = inspect.stack()
        caller_info = stack[1]  # This is a FrameInfo object

        # Extracting relevant information about the caller
        caller_frame = caller_info.frame
        caller_module = inspect.getmodule(caller_frame)
        module_name = caller_module.__name__ if caller_module else "<unknown>"
        file_name = caller_info.filename
        line_number = caller_info.lineno
        function_name = caller_info.function
        builtins._print(f"[INPUT] Called from {module_name} in {function_name} at {file_name}:{line_number}:", *args, **kwargs)
        builtins._input(*args, **kwargs)

    # Save the original print function
    builtins._input = builtins.input

    # Override the built-in print
    builtins.input = custom_input