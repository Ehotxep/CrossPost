import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from app import apply_update as U


class UpdateRestart(unittest.TestCase):
    def test_worker_poll_cannot_restart_sender_during_update(self):
        from app.api import Api
        api=Api.__new__(Api)
        api._updating=True
        with patch('app.worker.describe',return_value={'running':False}), patch('app.worker.ensure_running') as start:
            self.assertEqual(api.worker_status(),{'running':False})
            start.assert_not_called()

    def test_startup_failure_does_not_count_as_success(self):
        process=Mock(pid=123)
        process.poll.return_value=1
        with tempfile.TemporaryDirectory() as directory:
            ok,error=U.wait_for_restart(process,Path(directory),'1.2.3',timeout=.1)
        self.assertFalse(ok)
        self.assertIn('завершился',error)

    def test_restart_ignores_stale_instance_and_confirms_matching_process(self):
        process=Mock(pid=123)
        process.poll.return_value=None
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            info=dict(pid=999,version='1.2.3',url='http://127.0.0.1:1234/',token='local')
            (data/'app.instance').write_text(json.dumps(info))
            client=MagicMock()
            response=client.open.return_value.__enter__.return_value
            response.read.return_value=b'{"ok":true}'
            def update_instance(seconds):
                info['pid']=123
                (data/'app.instance').write_text(json.dumps(info))
            with patch('urllib.request.build_opener',return_value=client),patch.object(U.time,'sleep',side_effect=update_instance):
                ok,error=U.wait_for_restart(process,data,'1.2.3',timeout=1)
            self.assertTrue(ok,error)
            self.assertEqual(client.open.call_count,1)

    def test_restart_cleans_python_environment_and_captures_errors(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,PYTHONPATH='old-code',PYTHONHOME='wrong-python'),patch.object(U.subprocess,'Popen') as start:
            U.popen_detached(['python','-m','app'],directory,logfile=Path(directory)/'update.log')
            kwargs=start.call_args.kwargs
            self.assertNotIn('PYTHONPATH',kwargs['env'])
            self.assertNotIn('PYTHONHOME',kwargs['env'])
            self.assertEqual(kwargs['env']['PYTHONUTF8'],'1')
            self.assertIs(kwargs['stdout'],kwargs['stderr'])
