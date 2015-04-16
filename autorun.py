from git import Repo
import os
join = os.path.join

bare_repo = Repo.init(join(rw_dir, 'bare-repo'), bare=True)
assert bare_repo.bare
