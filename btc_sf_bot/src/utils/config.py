"""
Configuration Loader Module
"""
import os
import yaml
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv

from ..utils.logger import get_logger

logger = get_logger(__name__)


class Config:
    """Configuration loader."""
    
    def __init__(self, config_path: str = None):
        """
        Initialize configuration.
        
        Args:
            config_path: Path to config YAML file
        """
        # Load environment variables with explicit path
        env_path = Path(__file__).parent.parent.parent / "config" / ".env"
        load_dotenv(dotenv_path=env_path)
        
        # Default config path
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
        
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self._load_config()
    
    def _load_config(self):
        """Load configuration from YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                self._config = yaml.safe_load(f)
            
            # Replace environment variables
            self._replace_env_vars()
            
            logger.info(f"Configuration loaded from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise
    
    def _replace_env_vars(self):
        """Replace ${VAR} with environment variables."""
        def replace_value(value):
            if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                env_var = value[2:-1]
                return os.getenv(env_var, value)
            elif isinstance(value, dict):
                return {k: replace_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [replace_value(v) for v in value]
            return value
        
        self._config = replace_value(self._config)
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key.
        
        Args:
            key: Key in dot notation (e.g., 'exchange.name')
            default: Default value if key not found
        
        Returns:
            Configuration value
        """
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    def get_exchange(self) -> Dict[str, Any]:
        """Get exchange configuration."""
        return self._config.get('exchange', {})
    
    def get_trading(self) -> Dict[str, Any]:
        """Get trading configuration (checks for 'risk' or 'trading')."""
        return self._config.get('risk', self._config.get('trading', {}))
    
    def get_strategy(self) -> Dict[str, Any]:
        """Get strategy configuration."""
        return self._config.get('strategy', {})
    
    def get_stop_loss(self) -> Dict[str, Any]:
        """Get stop loss configuration."""
        return self._config.get('stop_loss', {})
    
    def get_take_profit(self) -> Dict[str, Any]:
        """Get take profit configuration."""
        return self._config.get('take_profit', {})
    
    def get_alerts(self) -> Dict[str, Any]:
        """Get alerts configuration."""
        return self._config.get('alerts', {})
    
    def get_server(self) -> Dict[str, Any]:
        """Get server configuration."""
        return self._config.get('server', {})
    
    def get_logging(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return self._config.get('logging', {})
    
    @property
    def config(self) -> Dict[str, Any]:
        """Get full configuration."""
        return self._config


# Global config instance
_config = None


def get_config(config_path: str = None) -> Config:
    """Get global config instance."""
    global _config
    if _config is None:
        _config = Config(config_path)
    return _config
