"""
Configuration Manager Module with validation and hot-reload capability.
"""
import os
import yaml
import time
from pathlib import Path
from typing import Any, Dict, Optional, Callable
from dotenv import load_dotenv
from pydantic import BaseModel, Field, validator
import threading

from ..utils.logger import get_logger

logger = get_logger(__name__)


# Pydantic models for configuration validation
class ExchangeConfig(BaseModel):
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "5m"
    htf_timeframe: str = "1h"
    htf_trend_ema_period: int = 50


class IPAConfig(BaseModel):
    score_threshold: int = Field(ge=1, le=20)
    ob_max_distance_atr: float = Field(ge=0.1, le=5.0)
    ob_body_min_pct: float = Field(ge=0.0001, le=0.01)
    h1_lookback_candles: int = Field(ge=10, le=200)
    volume_spike_min: float = Field(ge=0.5, le=5.0)
    rr_min: float = Field(ge=0.1, le=10.0)


class IOFConfig(BaseModel):
    score_threshold: int = Field(ge=1, le=20)
    der_min: float = Field(ge=0.1, le=1.0)
    der_strong: float = Field(ge=0.1, le=1.0)
    der_moderate: float = Field(ge=0.1, le=1.0)
    oi_change_min_pct: float = Field(ge=0.01, le=10.0)
    wall_threshold_asia: int = Field(ge=1000)
    wall_threshold_london: int = Field(ge=1000)
    wall_threshold_ny: int = Field(ge=1000)
    wall_max_distance_pct: float = Field(ge=0.001, le=0.1)
    min_wall_stability: int = Field(ge=5, le=300)
    wall_relative_size_multiplier: float = Field(ge=0.00001, le=0.1)
    absolute_min_wall_usd: int = Field(ge=1000)


class Config(BaseModel):
    exchange: Optional[ExchangeConfig] = Field(default_factory=ExchangeConfig)
    ipa: Optional[IPAConfig] = Field(default_factory=IPAConfig)
    iof: Optional[IOFConfig] = Field(default_factory=IOFConfig)
    
    # Additional sections can be added as needed
    
    class Config:
        # Allow extra fields for flexibility
        extra = "allow"


class ConfigManager:
    """Configuration manager with validation and hot-reload capability."""
    
    def __init__(self, config_path: str = None):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Path to config YAML file
        """
        self.config_path = Path(config_path) if config_path else \
            Path(__file__).parent.parent.parent / "config" / "config.yaml"
        self.env_path = Path(__file__).parent.parent.parent / "config" / ".env"
        self._config: Optional[Config] = None
        self._last_modified: float = 0
        self._reload_callbacks: list[Callable[[Config], None]] = []
        self._lock = threading.RLock()
        
        # Load environment variables
        load_dotenv(dotenv_path=self.env_path)
        
        # Initial load
        self.reload()
        
        logger.info(f"ConfigManager initialized with config: {self.config_path}")
    
    def _load_raw_config(self) -> Dict[str, Any]:
        """Load raw configuration from YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                raw_config = yaml.safe_load(f) or {}
            
            # Replace environment variables
            raw_config = self._replace_env_vars(raw_config)
            
            logger.info(f"Raw configuration loaded from {self.config_path}")
            return raw_config
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise
    
    def _replace_env_vars(self, value: Any) -> Any:
        """Replace ${VAR} with environment variables."""
        if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
            env_var = value[2:-1]
            return os.getenv(env_var, value)
        elif isinstance(value, dict):
            return {k: self._replace_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._replace_env_vars(v) for v in value]
        return value
    
    def _validate_and_create_config(self, raw_config: Dict[str, Any]) -> Config:
        """Validate raw config and create Config object."""
        try:
            config = Config(**raw_config)
            logger.info("Configuration validation successful")
            return config
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            raise
    
    def reload(self) -> bool:
        """
        Reload configuration from file if modified.
        
        Returns:
            True if config was reloaded, False otherwise
        """
        try:
            # Check if file exists and get modification time
            if not self.config_path.exists():
                logger.error(f"Config file not found: {self.config_path}")
                return False
                
            current_modified = self.config_path.stat().st_mtime
            
            # Only reload if file has been modified
            if current_modified <= self._last_modified:
                return False
            
            # Load and validate new config
            raw_config = self._load_raw_config()
            new_config = self._validate_and_create_config(raw_config)
            
            # Update config and notify callbacks
            with self._lock:
                old_config = self._config
                self._config = new_config
                self._last_modified = current_modified
            
            # Notify reload callbacks
            for callback in self._reload_callbacks:
                try:
                    callback(new_config)
                except Exception as e:
                    logger.error(f"Error in config reload callback: {e}")
            
            logger.info(f"Configuration reloaded from {self.config_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key in dot notation.
        
        Args:
            key: Key in dot notation (e.g., 'exchange.symbol')
            default: Default value if key not found
            
        Returns:
            Configuration value
        """
        # Ensure config is loaded
        if self._config is None:
            self.reload()
        
        # Reload if file has changed
        self.reload()
        
        # Navigate through nested keys
        keys = key.split('.')
        value = self._config
        
        try:
            for k in keys:
                if isinstance(value, BaseModel):
                    value = getattr(value, k)
                elif isinstance(value, dict):
                    value = value[k]
                else:
                    return default
            return value
        except (AttributeError, KeyError, TypeError):
            return default
    
    def get_exchange(self) -> ExchangeConfig:
        """Get exchange configuration."""
        return self.get('exchange', ExchangeConfig())
    
    def get_ipa(self) -> IPAConfig:
        """Get IPA configuration."""
        return self.get('ipa', IPAConfig())
    
    def get_iof(self) -> IOFConfig:
        """Get IOF configuration."""
        return self.get('iof', IOFConfig())
    
    def add_reload_callback(self, callback: Callable[[Config], None]) -> None:
        """
        Add a callback to be executed when configuration is reloaded.
        
        Args:
            callback: Function to call with new config
        """
        self._reload_callbacks.append(callback)
    
    def remove_reload_callback(self, callback: Callable[[Config], None]) -> None:
        """
        Remove a reload callback.
        
        Args:
            callback: Function to remove
        """
        if callback in self._reload_callbacks:
            self._reload_callbacks.remove(callback)
    
    @property
    def config(self) -> Optional[Config]:
        """Get full configuration object."""
        # Reload if file has changed
        self.reload()
        return self._config