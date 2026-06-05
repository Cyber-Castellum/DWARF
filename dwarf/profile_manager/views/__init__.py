"""View functions for the new /operate and /learn dashboards.

Each view function builds a context from the slice-1 data layer and
renders a Jinja2 template. View functions return HTML strings; they
do not write to the network. The HTTP handler in dashboard.py turns
the string into a 200 response.
"""
