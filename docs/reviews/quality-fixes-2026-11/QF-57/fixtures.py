"""Generated metadata for measure.py, kept beside it so the measurement runs on the parent too (the row's test file
does not exist there). The same shapes as tests/test_qf57_image_rights.py."""


def page(templates=(), licence="Public domain"):
    return {"title": "File:Artifact.jpg", "templates": [{"title": f"Template:{name}"} for name in templates],
            "imageinfo": [{"width": 3000, "height": 2400, "size": 100000, "mime": "image/jpeg",
                "url": "https://upload.wikimedia.org/fixture.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Artifact.jpg",
                "extmetadata": {"LicenseShortName": {"value": licence}, "Artist": {"value": "Fixture author"}}}]}


def flickr_item():
    return {"title": "Artifact", "url": "https://live.staticflickr.com/12/123_secret.jpg",
            "foreign_landing_url": "https://www.flickr.com/photos/fixture/123/", "license": "by",
            "license_version": "4.0", "provider": "flickr", "creator": "Old name"}


def flickr_get(license_id="4", public=1):
    def get(url, params=None, **kwargs):
        if "openverse" in url:
            return {"results": [flickr_item()]}
        if params["method"] == "flickr.photos.getInfo":
            return {"stat": "ok", "photo": {"id": "123", "license": license_id,
                "visibility": {"ispublic": public}, "owner": {"realname": "Current author"}}}
        return {"stat": "ok", "licenses": {"license": [
            {"id": "4", "url": "https://creativecommons.org/licenses/by/2.0/"},
            {"id": "0", "url": "https://www.flickr.com/all-rights-reserved/"}]}}
    return get
