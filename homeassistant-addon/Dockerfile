ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM $BUILD_FROM

ARG SERVER_REVISION=577772828c004168ee87f261146526db8119ac4d
RUN apk add --no-cache python3 py3-pip git \
    && git clone https://github.com/drytoad/NoLongerEvil-SelfHosted.git /server \
    && git -C /server checkout "$SERVER_REVISION" \
    && pip3 install --no-cache-dir --break-system-packages /server

COPY run.sh /run.sh
RUN sed -i 's/\r$//' /run.sh && chmod a+x /run.sh && mkdir -p /data
EXPOSE 8000 8082
CMD ["/run.sh"]
