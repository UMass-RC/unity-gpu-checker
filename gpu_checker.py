"""
Simon Leary
6/24/2022
GPU Checker
Loops with `sinfo` over nodes that are in both STATES_TO_CHECK and PARTITIONS_TO_CHECK
ssh's in using SSH_USER and SSH_PRIVKEY_FQN, tries to run `nvidia-smi`
If that fails in any way, send an email to EMAIL_TO from EMAIL_FROM (and put the node in DRAINING state)***
It actually sends two emails - one that there's an error and another that it's being put into DRAINING

CONFIG_FILE_PATH contains a cleartext password
    should be excluded from source control!
    should not be readable by any other user!
"""
import subprocess
import time
import smtplib
from email.message import EmailMessage
import configparser
import os

CONFIG_FILE_PATH = '/opt/gpu-checker/secretfile.txt'
CONFIG = None

def str_to_bool(string):
    if string.lower() in ['true', '1', 't', 'y', 'yes']:
        return True
    if string.lower() in ['false', '0', 'f', 'n', 'no']:
        return False
    return None

def multiline_str(*argv: str) -> str:
    """
    concat the strings and separate them with newlines
    with no indentation funny business!
    """
    string = ''
    for arg in argv:
        string = string + str(arg) + '\n'
    string = string[0:-1] # remove final newline
    return string

def purge_element(_list: list, elem_to_purge) -> list:
    return list(filter(lambda elem: elem != elem_to_purge, _list))

class ShellRunner:
    """
    spawn this with a shell command, then you have access to stdout, stderr, exit code,
    along with a boolean of whether or not the command was a success
    and if you use str(your_shell_runner), you get a formatted report of all the above
    """
    def __init__(self, command):
        # these should all get defined by self.run_shell_command
        self.last_shell_output = ''
        self.shell_error = ''
        self.exit_code = -1
        self.command_report = ''
        self.success = None

        self.run_shell_command(command)

    def __str__(self):
        return self.command_report

    def run_shell_command(self, command) -> None:
        """
        runs the command, defines the variables, quits
        """
        process = subprocess.run(
            command,
            capture_output=True,
            shell=True
        )
        # process.std* returns a bytes object, convert to string
        self.shell_output = str(process.stdout, 'UTF-8')
        self.shell_error = str(process.stderr, 'UTF-8')
        self.exit_code = process.returncode
        self.success = self.exit_code == 0
        self.command_report = multiline_str(
            "command:",
            command,
            f"command success: {self.success}",
            '',
            "stdout:",
            self.shell_output,
            '',
            "stderr:",
            self.shell_error,
            '',
            "exit code:",
            self.exit_code
        )

def find_slurm_nodes(states: str, partitions: str) -> None:
    """"
    return a list of node names that meet the specified states and partitions
    states and partitions are comma delimited strings
    """
    command = f"sinfo --states={states} --partition={partitions} -N --noheader"
    command_results = ShellRunner(command)
    success = command_results.success
    shell_output = command_results.shell_output
    command_report = str(command_results)

    if not success:
        raise Exception(command_report) # barf

    shell_output_lines = [line.replace('\n', '') for line in shell_output.split('\n')]
    shell_output_lines = purge_element(shell_output_lines, '')
    nodes = [line.split(' ')[0] for line in shell_output_lines]
    if len(nodes) == 0:
        print(f"no nodes found! {command}")
    return nodes

def drain_node(node: str, reason: str, do_send_email=True):
    """"
    tell slurm to put specified node into DRAINING state
    """
    command_results = ShellRunner(f"scontrol update nodename={node} state=drain reason=\"{reason}\"")
    success = command_results.success
    command_report = str(command_results)

    if do_send_email:
        email_to = CONFIG['email']['to']
        email_from = CONFIG['email']['from']
        if success:
            send_email(
                email_to,
                email_from,
                f"gpu-checker has drained node {node}",
                command_report
            )
        else:
            send_email(
                email_to,
                email_from,
                f"ACTION REQUIRED: gpu-checker wanted to drain node {node}, but failed",
                command_report
            )

def check_gpu(node: str, do_send_email=True) -> bool:
    """
    ssh into node and run `nvidia-smi`
    returns True if it works, false if it doesn't
    """
    ssh_user = CONFIG['ssh']['user']
    ssh_privkey = CONFIG['ssh']['keyfile']
    command = f"ssh {ssh_user}@{node} -o \"StrictHostKeyChecking=no\" -i {ssh_privkey} nvidia-smi && echo $? || echo $?"
    command_results = ShellRunner(command)
    shell_output = command_results.shell_output
    command_report = str(command_results)

    # find exit code that was put into stdout when I said `echo $?`
    shell_output_lines = [line.replace('\n', '') for line in shell_output.split('\n')]
    shell_output_lines = purge_element(shell_output_lines, '')
    ssh_exit_code = shell_output_lines[-1]

    success = command_results.success and int(ssh_exit_code) == 0

    if not success and do_send_email:
        email_to = CONFIG['email']['to']
        email_from = CONFIG['email']['from']
        send_email(
            email_to,
            email_from,
            f"gpu-checker has detected an error on node {node}",
            command_report
        )

    if success:
        print(f"gpu works on node {node}")
    return success

def send_email(to: str, _from: str, subject: str, body: str):
    """
    send an email using an SMTP server on localhost
    """
    body = multiline_str(
        body,
        CONFIG['email']['signature']
    )
    print(
        "sending email:___________________________________",
        f"to: {to}",
        f"from: {_from}",
        f"subject: {subject}",
        "body:",
        body,
        sep='\n'
    )
    msg = EmailMessage()
    msg.set_content(body)
    msg['To'] = to
    msg['From'] = _from
    msg['Subject'] = subject

    hostname = CONFIG['smtp_auth']['hostname']
    port = int(CONFIG['smtp_auth']['port'])
    user = CONFIG['smtp_auth']['user']
    password = CONFIG['smtp_auth']['password']
    is_ssl = str_to_bool(CONFIG['smtp_auth']['is_ssl'])

    if is_ssl:
        s = smtplib.SMTP_SSL(hostname, port, timeout=5)
    else:
        s = smtplib.SMTP(hostname, port, timeout=5)
    s.login(user, password)
    s.send_message(msg)
    s.quit()

    print("email sent successfully!____________________________________________________")

if __name__=="__main__":
    CONFIG = configparser.ConfigParser()
    if os.path.isfile(CONFIG_FILE_PATH):
        CONFIG.read(CONFIG_FILE_PATH)
    else:
        # write default empty config file
        CONFIG['nodes'] = {
            "states_to_check" : "mixed,idle",
            "partitions_to_check" : "gpu"
        }
        CONFIG['ssh'] = {
            "user" : "",
            "keyfile" : ""
        }
        CONFIG['email'] = {
            "enabled" : "False",
            "to" : "",
            "from" : "",
            "signature" : ""
        }
        CONFIG['smtp_auth'] = {
            "hostname" : "",
            "port" : "",
            "user" : "",
            "password" : "",
            "is_ssl" : "False"
        }
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as config_file:
            CONFIG.write(config_file)

    states = CONFIG['nodes']['states_to_check']
    partitions = CONFIG['nodes']['partitions_to_check']
    do_send_email = str_to_bool(CONFIG['email']['enabled'])
    while True:
        for node in find_slurm_nodes(states, partitions):
            gpu_works = check_gpu(node, do_send_email=do_send_email)
            if not gpu_works:
                drain_node(node, 'nvidia-smi failure', do_send_email=do_send_email)
                pass
            # each loop takes about 5 seconds on its own, most of the delay is the ssh command
            time.sleep(60)
