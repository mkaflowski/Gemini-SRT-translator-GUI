"""
TMDB API helper for fetching movie information and IDs.
"""

import requests
import json
from urllib.parse import quote


class TMDBHelper:
    """Helper class for TMDB API operations"""

    def __init__(self, api_key, logger=None):
        self.api_key = api_key
        self.logger = logger
        self.base_url = "https://api.themoviedb.org/3"
        self.session = requests.Session()

    def log(self, message):
        """Log a message"""
        if self.logger:
            self.logger(message)
        else:
            print(message)

    def search_movie(self, title, year=None, limit=5):
        """
        Search for a movie by title and optional year.

        Args:
            title (str): Movie title to search for
            year (str/int): Release year (optional)
            limit (int): Maximum number of results to return

        Returns:
            list: List of movie results with id, title, year, overview
        """
        if not self.api_key:
            self.log("❌ TMDB API key not provided")
            return []

        if not title or not title.strip():
            self.log("❌ Movie title is empty")
            return []

        try:
            # Prepare search parameters
            params = {
                'api_key': self.api_key,
                'query': title.strip(),
                'language': 'en-US',
                'include_adult': 'false'
            }

            # Add year if provided
            if year:
                try:
                    year_int = int(year)
                    if 1900 <= year_int <= 2030:  # Reasonable year range
                        params['year'] = year_int
                        self.log(f"🔍 Searching for '{title}' ({year})...")
                    else:
                        self.log(f"⚠️ Invalid year {year}, searching without year filter")
                        self.log(f"🔍 Searching for '{title}'...")
                except (ValueError, TypeError):
                    self.log(f"⚠️ Invalid year format {year}, searching without year filter")
                    self.log(f"🔍 Searching for '{title}'...")
            else:
                self.log(f"🔍 Searching for '{title}'...")

            # Make API request
            url = f"{self.base_url}/search/movie"
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            results = data.get('results', [])

            if not results:
                self.log(f"❌ No movies found for '{title}'")
                return []

            self.log(f"✅ Found {len(results)} results")

            # Process and limit results
            processed_results = []
            for i, movie in enumerate(results[:limit]):
                processed_movie = {
                    'id': movie.get('id'),
                    'title': movie.get('title', 'Unknown Title'),
                    'release_date': movie.get('release_date', ''),
                    'year': self._extract_year_from_date(movie.get('release_date', '')),
                    'overview': movie.get('overview', 'No overview available'),
                    'poster_path': movie.get('poster_path', ''),
                    'vote_average': movie.get('vote_average', 0),
                    'popularity': movie.get('popularity', 0)
                }
                processed_results.append(processed_movie)

                # Log each result
                year_str = f"({processed_movie['year']})" if processed_movie['year'] else "(Unknown year)"
                self.log(f"   {i + 1}. {processed_movie['title']} {year_str} - ID: {processed_movie['id']}")

            return processed_results

        except requests.exceptions.RequestException as e:
            self.log(f"❌ Network error searching for movie: {e}")
            return []
        except json.JSONDecodeError as e:
            self.log(f"❌ Error parsing TMDB response: {e}")
            return []
        except Exception as e:
            self.log(f"❌ Unexpected error searching for movie: {e}")
            return []

    def get_movie_details(self, movie_id):
        """
        Get detailed information for a specific movie ID.

        Args:
            movie_id (int): TMDB movie ID

        Returns:
            dict: Movie details or None if error
        """
        if not self.api_key:
            self.log("❌ TMDB API key not provided")
            return None

        try:
            params = {
                'api_key': self.api_key,
                'language': 'en-US'
            }

            url = f"{self.base_url}/movie/{movie_id}"
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()

            movie = response.json()

            # Process the detailed movie information
            details = {
                'id': movie.get('id'),
                'title': movie.get('title', 'Unknown Title'),
                'original_title': movie.get('original_title', ''),
                'release_date': movie.get('release_date', ''),
                'year': self._extract_year_from_date(movie.get('release_date', '')),
                'overview': movie.get('overview', ''),
                'runtime': movie.get('runtime', 0),
                'genres': [genre.get('name', '') for genre in movie.get('genres', [])],
                'vote_average': movie.get('vote_average', 0),
                'vote_count': movie.get('vote_count', 0),
                'popularity': movie.get('popularity', 0),
                'poster_path': movie.get('poster_path', ''),
                'backdrop_path': movie.get('backdrop_path', ''),
                'imdb_id': movie.get('imdb_id', ''),
                'tagline': movie.get('tagline', ''),
                'status': movie.get('status', ''),
                'budget': movie.get('budget', 0),
                'revenue': movie.get('revenue', 0)
            }

            return details

        except requests.exceptions.RequestException as e:
            self.log(f"❌ Network error getting movie details: {e}")
            return None
        except json.JSONDecodeError as e:
            self.log(f"❌ Error parsing TMDB response: {e}")
            return None
        except Exception as e:
            self.log(f"❌ Unexpected error getting movie details: {e}")
            return None

    def find_best_match(self, title, year=None):
        """
        Find the best matching movie for a title and year.

        Args:
            title (str): Movie title
            year (str/int): Release year (optional)

        Returns:
            dict: Best matching movie or None
        """
        results = self.search_movie(title, year, limit=10)

        if not results:
            return None

        # If year is provided, try to find exact year match first
        if year:
            try:
                target_year = int(year)
                for movie in results:
                    if movie['year'] and int(movie['year']) == target_year:
                        self.log(f"🎯 Found exact year match: {movie['title']} ({movie['year']})")
                        return movie
            except (ValueError, TypeError):
                pass

        # If no exact year match or no year provided, return first result (most popular/relevant)
        best_match = results[0]
        self.log(f"🎯 Best match: {best_match['title']} ({best_match['year']}) - ID: {best_match['id']}")
        return best_match

    def _extract_year_from_date(self, date_string):
        """Extract year from TMDB date string (YYYY-MM-DD format)"""
        if not date_string:
            return None

        try:
            return date_string.split('-')[0]
        except:
            return None

    def test_api_key(self):
        """
        Test if the API key is valid by making a simple request.

        Returns:
            bool: True if API key is valid, False otherwise
        """
        if not self.api_key:
            return False

        try:
            params = {'api_key': self.api_key}
            url = f"{self.base_url}/configuration"
            response = self.session.get(url, params=params, timeout=5)

            if response.status_code == 200:
                self.log("✅ TMDB API key is valid")
                return True
            elif response.status_code == 401:
                self.log("❌ TMDB API key is invalid")
                return False
            else:
                self.log(f"⚠️ TMDB API returned status code: {response.status_code}")
                return False

        except Exception as e:
            self.log(f"❌ Error testing TMDB API key: {e}")
            return False


def get_tmdb_id_for_file(filename, tmdb_api_key, logger=None):
    """
    Convenience function to get TMDB ID for a filename.

    Args:
        filename (str): Filename to extract movie info from
        tmdb_api_key (str): TMDB API key
        logger (callable): Optional logging function

    Returns:
        tuple: (tmdb_id, movie_info) or (None, None) if not found
    """
    if not tmdb_api_key or not tmdb_api_key.strip():
        if logger:
            logger("❌ TMDB API key not provided")
        return None, None

    # Import here to avoid circular imports
    try:
        from .file_utils import extract_movie_info
    except ImportError:
        from utils.file_utils import extract_movie_info

    # Extract movie information from filename
    title, year = extract_movie_info(filename)

    if not title or title == "Unknown Movie":
        if logger:
            logger(f"❌ Could not extract movie title from filename: {filename}")
        return None, None

    # Search TMDB
    tmdb = TMDBHelper(tmdb_api_key, logger)

    # Test API key first
    if not tmdb.test_api_key():
        return None, None

    # Find best match
    movie = tmdb.find_best_match(title, year)

    if movie:
        return movie['id'], movie
    else:
        return None, None