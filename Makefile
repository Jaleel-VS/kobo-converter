IMAGE := kobo-converter
PORT  := 8000

build:
	docker build -t $(IMAGE) .

run: build
	docker run --rm -p $(PORT):$(PORT) \
		-e PORT=$(PORT) \
		-e AWS_ACCESS_KEY_ID \
		-e AWS_SECRET_ACCESS_KEY \
		-e AWS_DEFAULT_REGION=us-east-1 \
		-e S3_BUCKET=kobo-converter-195950944512 \
		$(IMAGE)
