#!/usr/bin/env bash

exec caffeinate -dimsu -t 86400 bash "$(dirname "$0")/scrape_data.sh"