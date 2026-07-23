import os
import importlib
import pkgutil
import inspect
from loguru import logger
from modules.base_module import BaseModule

class PluginLoader:
    """
    Auto-discovers and loads all BaseModule subclasses from the modules/ directory.
    """
    _REGISTRY = {}
    _is_loaded = False
    
    @classmethod
    def reload(cls, modules_pkg="modules"):
        """Forces a reload of all plugins."""
        cls._is_loaded = False
        cls._REGISTRY.clear()
        return cls.discover(modules_pkg)
        
    @classmethod
    def discover(cls, modules_pkg="modules"):
        if cls._is_loaded:
            return cls._REGISTRY
            
        logger.info(f"Discovering tool modules in '{modules_pkg}'...")
        
        try:
            # Import the base package
            base_pkg = importlib.import_module(modules_pkg)
        except ImportError as e:
            logger.error(f"Failed to import base package {modules_pkg}: {e}")
            return cls._REGISTRY
            
        for importer, modname, ispkg in pkgutil.walk_packages(base_pkg.__path__, prefix=f"{modules_pkg}."):
            try:
                mod = importlib.import_module(modname)
                for name, obj in inspect.getmembers(mod, inspect.isclass):
                    # Find subclasses of BaseModule but exclude BaseModule itself
                    if issubclass(obj, BaseModule) and obj is not BaseModule:
                        # Use TOOL_NAME if available, else derive from class name
                        tool_name = getattr(obj, 'TOOL_NAME', cls._to_snake(name))
                        cls._REGISTRY[tool_name] = obj
                        logger.debug(f"Registered module: {tool_name} ({name})")
            except Exception as e:
                logger.warning(f"Failed to load module {modname}: {e}")
                
        cls._is_loaded = True
        logger.info(f"Discovered {len(cls._REGISTRY)} tool modules.")
        return cls._REGISTRY
        
    @classmethod
    def get(cls, tool_name: str):
        if not cls._is_loaded:
            cls.discover()
        return cls._REGISTRY.get(tool_name)
        
    @classmethod
    def list_all(cls) -> list:
        if not cls._is_loaded:
            cls.discover()
        return list(cls._REGISTRY.keys())
        
    @classmethod
    def list_by_phase(cls) -> dict:
        if not cls._is_loaded:
            cls.discover()
            
        phases = {}
        for name, obj in cls._REGISTRY.items():
            phase = getattr(obj, 'PHASE', 'uncategorized')
            if phase not in phases:
                phases[phase] = []
            phases[phase].append(name)
        return phases

    @staticmethod
    def _to_snake(name: str) -> str:
        import re
        name = re.sub(r'Module$', '', name)
        return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
