"""
Base classes for agent tools
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any
import os
from agents.utils.exceptions import InvalidToolInputError


class BaseTool(ABC):
    """Abstract base class for all agent tools"""
    
    def __init__(self):
        self._description = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name for Claude function calling"""
        pass
    
    @property
    @abstractmethod
    def input_schema(self) -> Dict[str, Any]:
        """Tool input schema for Claude function calling"""
        pass
    
    @property
    def description(self) -> str:
        """Tool description loaded from markdown file"""
        if self._description is None:
            self._description = self._load_description()
        return self._description
    
    def _load_description(self) -> str:
        """Load description from description.md file"""
        import inspect
        tool_file = inspect.getfile(self.__class__)
        tool_dir = os.path.dirname(tool_file)
        description_path = os.path.join(tool_dir, "description.md")
        
        try:
            with open(description_path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except FileNotFoundError:
            return f"Description file not found for {self.name}"
        except Exception as e:
            return f"Error loading description for {self.name}: {str(e)}"
    
    def to_claude_tool(self) -> Dict[str, Any]:
        """Convert tool to Claude function calling format"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema
        }

    def validate_input(self, input: Dict[str, Any]) -> None:
        """Validate the tool input against the input schema
        
        Args:
            input: The input dictionary to validate
            
        Returns:
            None
            
        Raises:
            InvalidToolInputError: If validation fails
        """
        schema = self.input_schema
        
        # Validate top-level type
        if schema.get("type") == "object":
            if not isinstance(input, dict):
                raise InvalidToolInputError(f"{self.name}: Input must be an object, got {type(input).__name__}")
        
        # Check required properties
        required = schema.get("required", [])
        for prop in required:
            if prop not in input:
                raise InvalidToolInputError(f"{self.name}: Missing required property '{prop}'")
        
        # Check for additional properties if not allowed
        if schema.get("additionalProperties") is False:
            properties = schema.get("properties", {})
            for key in input.keys():
                if key not in properties:
                    raise InvalidToolInputError(
                        f"{self.name}: Unexpected property '{key}'. Allowed properties: {list(properties.keys())}"
                    )
        
        # Validate each property
        properties = schema.get("properties", {})
        for key, value in input.items():
            if key in properties:
                self._validate_property(key, value, properties[key])
        
    
    def _validate_property(self, name: str, value: Any, prop_schema: Dict[str, Any]):
        """Validate a single property against its schema
        
        Args:
            name: Property name
            value: Property value
            prop_schema: Schema definition for this property
            
        Raises:
            InvalidToolInputError: If validation fails
        """
        expected_type = prop_schema.get("type")
        
        # Type validation
        if expected_type == "string":
            if not isinstance(value, str):
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be a string, got {type(value).__name__}"
                )
            # Check minLength
            if "minLength" in prop_schema and len(value) < prop_schema["minLength"]:
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must have at least {prop_schema['minLength']} characters"
                )
            # Check maxLength
            if "maxLength" in prop_schema and len(value) > prop_schema["maxLength"]:
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must have at most {prop_schema['maxLength']} characters"
                )
        
        elif expected_type == "number" or expected_type == "integer":
            if not isinstance(value, (int, float)):
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be a number, got {type(value).__name__}"
                )
            if expected_type == "integer" and not isinstance(value, int):
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be an integer, got {type(value).__name__}"
                )
            # Check minimum
            if "minimum" in prop_schema and value < prop_schema["minimum"]:
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be at least {prop_schema['minimum']}"
                )
            # Check maximum
            if "maximum" in prop_schema and value > prop_schema["maximum"]:
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be at most {prop_schema['maximum']}"
                )
        
        elif expected_type == "boolean":
            if not isinstance(value, bool):
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be a boolean, got {type(value).__name__}"
                )
        
        elif expected_type == "array":
            if not isinstance(value, list):
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be an array, got {type(value).__name__}"
                )
            # Check minItems
            if "minItems" in prop_schema and len(value) < prop_schema["minItems"]:
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must have at least {prop_schema['minItems']} items"
                )
            # Check maxItems
            if "maxItems" in prop_schema and len(value) > prop_schema["maxItems"]:
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must have at most {prop_schema['maxItems']} items"
                )
            # Validate items if schema is provided
            if "items" in prop_schema:
                items_schema = prop_schema["items"]
                for i, item in enumerate(value):
                    self._validate_property(f"{name}[{i}]", item, items_schema)
        
        elif expected_type == "object":
            if not isinstance(value, dict):
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be an object, got {type(value).__name__}"
                )
            # Recursively validate nested object if properties are defined
            if "properties" in prop_schema:
                nested_properties = prop_schema["properties"]
                nested_required = prop_schema.get("required", [])
                
                # Check required nested properties
                for nested_prop in nested_required:
                    if nested_prop not in value:
                        raise InvalidToolInputError(
                            f"{self.name}: Property '{name}.{nested_prop}' is required"
                        )
                
                # Validate each nested property
                for nested_key, nested_value in value.items():
                    if nested_key in nested_properties:
                        self._validate_property(
                            f"{name}.{nested_key}", 
                            nested_value, 
                            nested_properties[nested_key]
                        )
        
        # Enum validation
        if "enum" in prop_schema:
            if value not in prop_schema["enum"]:
                raise InvalidToolInputError(
                    f"{self.name}: Property '{name}' must be one of {prop_schema['enum']}, got '{value}'"
                )
