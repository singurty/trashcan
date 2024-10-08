import multiprocessing

bind = "0.0.0.0:80"
workers = multiprocessing.cpu_count() * 2 + 1
timeout = 0
accesslog = "./gunicorn.access.log"
errorlog = "./gunicorn.error.log"