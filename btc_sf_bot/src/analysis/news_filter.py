"""
News Filter Module for checking high-impact economic events.
"""
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
import requests
from ..utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)

class NewsFilter:
    """Filter trading based on high-impact news events."""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.enabled = self.config.get('news_filter_enabled', True)
        self.pause_before_minutes = self.config.get('news_pause_before', 15)
        self.pause_after_minutes = self.config.get('news_pause_after', 15)
        self.impact_levels = self.config.get('news_impact_levels', ['HIGH', 'CRITICAL'])
        
        # Cache for news events
        self.news_cache: List[Dict] = []
        self.last_update: Optional[datetime] = None
        self.update_interval = timedelta(hours=24)  # Once per day is sufficient for scheduled news
        
    @log_errors
    @timed_metric("NewsFilter.update_news")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def update_news(self):
        """Update news events from a public fiscal calendar API."""
        if self.last_update and datetime.now(timezone.utc) - self.last_update < self.update_interval:
            return

        try:
            # url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            # Using a more reliable backup or the provided URL
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                fetched_news = []
                
                for item in data:
                    country = item.get('country', '')
                    impact = item.get('impact', 'Low').upper()
                    title = item.get('title', 'Unknown Event')
                    
                    # Filter for USD/BTC and specified impact levels
                    if (country in ['USD', 'BTC']) and impact in self.impact_levels:
                        # Parsing date: "2026-03-11T08:30:00-05:00"
                        date_str = item.get('date')
                        if not date_str:
                            continue
                            
                        try:
                            # Handle ISO format with timezone offset
                            event_time = datetime.fromisoformat(date_str)
                            # Convert to UTC
                            event_time_utc = event_time.astimezone(timezone.utc)
                            
                            fetched_news.append({
                                'time': event_time_utc,
                                'title': title,
                                'impact': impact,
                                'country': country
                            })
                        except (ValueError, TypeError) as e:
                            logger.error(f"Error parsing news date {date_str}: {e}")
                            continue
                
                self.news_cache = fetched_news
                was_updated = self.last_update is None or (datetime.now(timezone.utc) - self.last_update) >= self.update_interval
                self.last_update = datetime.now(timezone.utc)
                if was_updated and fetched_news:
                    # Filter to show UPCOMING and ACTIVE news
                    now = datetime.now(timezone.utc)
                    active_news = [n for n in fetched_news 
                                  if n['time'] + timedelta(minutes=self.pause_after_minutes) > now]
                    
                    if active_news:
                        active_news.sort(key=lambda x: x['time'])
                        news_list = []
                        for n in active_news[:5]:  # Show max 5 events
                            time_str = n['time'].strftime('%H:%M UTC')
                            prefix = "▶️ " if n['time'] <= now else "📅 "
                            news_list.append(f"{prefix}{time_str} {n['title'][:30]}")
                        logger.debug(f"📰 News: {' | '.join(news_list)}")
            
        except Exception as e:
            logger.error(f"Error updating news: {e}")
            
    @log_errors
    @timed_metric("NewsFilter.is_news_paused")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def is_news_paused(self) -> Tuple[bool, Optional[Dict]]:
        """
        Check if trading should be paused due to upcoming news.
        
        Returns:
            Tuple of (is_paused, news_event)
        """
        if not self.enabled:
            return False, None
            
        self.update_news()
        
        now = datetime.now(timezone.utc)
        
        for event in self.news_cache:
            event_time = event.get('time')
            if not event_time:
                continue
                
            impact = event.get('impact', 'LOW').upper()
            if impact not in self.impact_levels:
                continue
                
            # Check 15 mins before and 15 mins after
            start_pause = event_time - timedelta(minutes=self.pause_before_minutes)
            end_pause = event_time + timedelta(minutes=self.pause_after_minutes)
            
            if start_pause <= now <= end_pause:
                return True, event
                
        return False, None

    @log_errors
    @timed_metric("NewsFilter.add_manual_event")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def add_manual_event(self, timestamp: datetime, title: str, impact: str = 'HIGH'):
        """Manually add an event to the filter (useful for testing or specific crypto events)."""
        self.news_cache.append({
            'time': timestamp,
            'title': title,
            'impact': impact
        })

    def is_critical_news_upcoming(self, threshold_minutes: int = 10) -> Tuple[bool, Optional[Dict]]:
        """
        Check if high-impact news is extremely close (e.g. 10 mins).
        Used for auto-closing open positions.
        """
        if not self.enabled:
            return False, None
            
        now = datetime.now(timezone.utc)
        for event in self.news_cache:
            event_time = event.get('time')
            if not event_time: continue
            
            impact = event.get('impact', 'LOW').upper()
            if impact in ['HIGH', 'CRITICAL']:
                time_to_event = (event_time - now).total_seconds() / 60
                if 0 <= time_to_event <= threshold_minutes:
                    return True, event
                    
        return False, None


