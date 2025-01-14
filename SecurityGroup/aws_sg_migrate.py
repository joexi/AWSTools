#!/usr/bin/python

import sys, os, stat, re, subprocess, getopt, json

def checkRegionVpc(sourceRegion, destRegion): 

    data = {}
    if sourceRegion is None or destRegion is None:
        cmd = [ 'aws', 'ec2', 'describe-regions' ]
        ap = subprocess.check_output(cmd)
        data = json.loads(ap.decode('utf-8'))
    else:
        data = {'Regions': [{"RegionName": sourceRegion}, {"RegionName": destRegion}]}

    if 'Regions' not in data:
        print("Internal error: no Regions in return structure")
        sys.exit(3)

    regions_vpcs = {}
    regions_dict = data['Regions']
    regions = list(map(lambda x : x['RegionName'], regions_dict))
    
    for region in regions_dict:
        vpcs = []
        cmd = [ 'aws', 'ec2', 'describe-vpcs', '--region=%s' % region["RegionName"] ]
        ap = subprocess.check_output(cmd)
        data = json.loads(ap.decode('utf-8'))
        vpcs_dict = data['Vpcs']
        for vpc_data in vpcs_dict:
            vpcs.append(vpc_data['VpcId'])
        regions_vpcs[region["RegionName"]] = vpcs
    # regions_vpcs = dict(zip(regions, vpcs))

    # print(json.dumps(regions_vpcs, indent=2))

    return regions_vpcs

def makesg(profile, sgid, vpcid, source, destin, shell):

    regions_vpcs = checkRegionVpc(source, destin)

    # sys.exit(1)

    script = open('%s.sh' % sgid, 'w')
    
    cmd = [ 'aws', 'ec2', 'describe-security-groups', '--region=%s' % source, '--output=json', ]
    if source:
        cmd.append("--group-id=%s" % (sgid))
    else:
        cmd = [ 'aws', 'ec2', 'describe-security-groups', '--group-id=%s' % sgid, '--output=json', ]
    
    if profile:
        cmd.append('--profile')
        cmd.append(profile)
    
    ap = subprocess.check_output(cmd)

    data = json.loads(ap.decode('utf-8'))
    
    if 'SecurityGroups' not in data:
        print("Internal error: no SecurityGroups key in data")
        sys.exit(3)
    sg1 = data['SecurityGroups'][0]

    # print(json.dumps(sg1, indent=2))

    groupName = sg1['GroupName'] #+ '_migrated'
    groupDesc = sg1['Description']
    groupTags = sg1['Tags'] if 'Tags' in sg1 else []
    
    # Sanity check
    perms = sg1['IpPermissions'] + sg1['IpPermissionsEgress']
    for ipp in perms:
        if 'FromPort' not in ipp:   continue
        if 'IpProtocol' not in ipp:   continue
        if 'IpRanges' not in ipp:   continue
        if 'ToPort' not in ipp:   continue
        if len(ipp['UserIdGroupPairs']) > 0:
            sys.stderr.write("Warning: ignoring User Id info\n")
        for ipr in ipp['IpRanges']:
            for k in ipr.keys():
                if k != 'CidrIp' and k != 'Description':
                    sys.stderr.write("Error: Don't know how to handle")
                    sys.stderr.write("key %s in IpRanges\n" % (k))
                    sys.exit(4)

    if vpcid in regions_vpcs[destin]:
        print(True)
    else:
        sys.stderr.write("Error: vpcid not in destin region")
        sys.exit(4)

    destinations = []

    if shell:
        print("# Commands auto-generated by the copysg.py script", file=script)
        print(" ", file=script)
    destinations = regions_vpcs
    cmd.append("--filters=Name=group-name,Values='%s'" % (groupName))
    
    if destin is not None:
        filtered = {dest:vpc for dest,vpc in destinations.items() if dest not in [source] and dest == destin and vpcid in vpc}
    else:    
        filtered = {dest:vpc for dest,vpc in destinations.items() if dest not in [source] and vpcid in vpc}

    print(destinations)
    print(filtered)

    for dest,vpc in filtered.items():
        print(dest)
        print(vpc)
        ap = subprocess.check_output(cmd)
        if ap is not None:
            delete_cmd = "aws ec2 delete-security-group"
        create_cmd = "aws ec2 create-security-group --vpc-id=%s" % (vpcid)
        cmd_existing = [ 'aws', 'ec2', 'describe-security-groups', '--region=%s' % dest, "--query=SecurityGroups[].GroupName" ]
        ap = subprocess.check_output(cmd_existing)
        sgNames = json.loads(ap)
        if shell:
            if ap is not None:
                if groupName != 'default' and groupName in sgNames:
                    print("sgout=(`%s --group-name='%s' --region %s --output table`)" % (delete_cmd, groupName, dest), file=script)
                    print('if [ $? != 0 ]; then', file=script)
                    print('   echo "Error: %s failed"' % (delete_cmd), file=script)
                    print('   exit 1', file=script)
                    print('fi', file=script)       
            print("sgout=(`%s --group-name='%s' --region %s --description='%s' --output table`)" % (create_cmd, groupName, dest, groupDesc), file=script)
            print('if [ $? != 0 ]; then', file=script)
            print('   echo "Error: %s failed"' % (create_cmd), file=script)
            print('   exit 1', file=script)
            print('fi', file=script)
            print('if [ "${sgout[6]}" != \'GroupId\' ]; then', file=script)
            print('   echo "Error: expected \'GroupId\', got ${sgout[6]}"', file=script)
            print('   exit 1', file=script)
            print('fi', file=script)
            print('SGID=${sgout[8]}', file=script)
        else:
            if groupName != 'default' and groupName in sgNames:
                print("%s --group-name='%s' --region %s" % (delete_cmd, groupName, dest), file=script)
            print("sgcreate=$(%s --group-name='%s' --region %s --description='%s')" % (create_cmd, groupName, dest, groupDesc), file=script)
            print("sgid=$(echo $sgcreate|sed 's/{//g;s/}//g;s/\"//g'|cut -d ':' -f2)", file=script)

        for ipp in sg1['IpPermissions']:
            if 'FromPort' not in ipp:   continue
            if 'IpProtocol' not in ipp:   continue
            if 'IpRanges' not in ipp:   continue
            if 'ToPort' not in ipp:   continue
            for ipr in ipp['IpRanges']:
                cidr = ipr['CidrIp']

                auth_cmd = "aws ec2 authorize-security-group-ingress"
                if shell:
                    print("%s --region %s --group-id=$SGID --protocol='%s'" % (auth_cmd, dest, ipp['IpProtocol']),end=" ", file=script)
                else:
                    print("%s --region %s --group-name=%s --protocol='%s'" % (auth_cmd, dest, groupName, ipp['IpProtocol']),end=" ", file=script)
                if ipp['ToPort'] < 0:
                    ipp['ToPort'] = ipp['FromPort']
                if ipp['FromPort'] != ipp['ToPort']:
                    print("--port=%s-%s" % (ipp['FromPort'], ipp['ToPort']),end=" ", file=script)
                else:
                    print("--port=%s" % (ipp['FromPort']),end=" ", file=script)
                    print("--cidr=%s" % (ipr['CidrIp']), file=script)
                if shell:
                    print('if [ $? != 0 ]; then', file=script)
                    print('   echo "Error: %s failed"' % (auth_cmd), file=script)
                    print('   exit 1', file=script)
                    print('fi', file=script)

        for ipp in sg1['IpPermissionsEgress']:
            if 'FromPort' not in ipp:   continue
            if 'IpProtocol' not in ipp:   continue
            if 'IpRanges' not in ipp:   continue
            if 'ToPort' not in ipp:   continue
            for ipr in ipp['IpRanges']:
                cidr = ipr['CidrIp']

                auth_cmd = "aws ec2 authorize-security-group-egress"
                if shell:
                    print("%s --region %s --group-id=$SGID --protocol='%s'" % (auth_cmd, dest, ipp['IpProtocol']), end=" ", file=script)
                else:
                    print("%s --region %s --group-id=$SGID --protocol='%s'" % (auth_cmd, dest, ipp['IpProtocol']), end=" ", file=script)
                if ipp['ToPort'] < 0:
                    # ICMP ToPort was -1 ???
                    ipp['ToPort'] = ipp['FromPort']
                if ipp['FromPort'] != ipp['ToPort']:
                    print("--port=%s-%s" % (ipp['FromPort'], ipp['ToPort']),end=" ", file=script)
                else:
                    print("--port=%s" % (ipp['FromPort']),end=" ", file=script)
                    print("--cidr=%s" % (ipr['CidrIp']), file=script)
                if shell:
                    print('if [ $? != 0 ]; then')
                    print('   echo "Error: %s failed"' % (auth_cmd))
                    print('   exit 1', file=script)
                    print('fi', file=script)

        for tag in groupTags:
            if shell:
                print("aws ec2 create-tags --region %s --resources $SGID" % (dest), end=" ", file=script)
                print('--tags "Key=%s,Value=%s"' % (tag['Key'], tag['Value']), file=script)
            else:
                print("aws ec2 create-tags --region %s --resources $sgid" % (dest), end=" ", file=script)
                print('--tags "Key=%s,Value=%s"' % (tag['Key'], tag['Value']), file=script)

        # setting script permissions
        os.chmod(script.name, 0o755)
    
    print("run scipt {}".format(script.name))    
    script.close()
    run_script_call = subprocess.Popen(['/bin/sh',"./{}".format(script.name)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    run_script_call.wait()
    print("OUT: {}".format(run_script_call.stdout.read()))
    print("ERR: {}".format(run_script_call.stderr.read()))

        

###############################  MAIN  #######################################


def main():
    try:
        opts, args = getopt.getopt(sys.argv[1:], "hp:sv:", [ "help", "profile=", "shell", "vpc=", "src=", "dest=", ])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    
    profile = None
    vpcid = None
    shell = False
    source = None
    destination = None
    for o,a in opts:
        if o in ("-h", "--help"):
            usage()
            return
        if o in ("-p", "--profile"):
            profile = a
        if o in ("-s", "--shell"):
            shell = True
        if o in ("-sc", "--src"):
            source = a
        if o in ("-ds", "--dest"):
            destination = a
        if o in ("-v", "--vpc"):
            vpcid = a
    
    if len(args) != 1:
        print("ERROR: You must give a security group id")
        usage()
        sys.exit(1)
    sgids = args[0]
    sgid_arr = sgids.split(",")

    for sgid in sgid_arr:
        print(sgid)
        makesg(profile, sgid, vpcid, source, destination, shell)

############################## USAGE #########################################

def usage():

    print("aws_sg_migrate.py [-h] [--profile=alt_profile] [--shell] [--vpc=vpcid] [-src=source_region] [--dest=dest_region]", end=" ")
    print("sg_id")
    print("    -h - help")
    print("    --profile (or -p) - use alternate aws cli profile")
    print("    --shell (or -s)   - wrap commands in shell syntax to capture id")
    print("    --vpc   (or -v)   - specify destination VPC ID for new SG")
    print("    --src   (or -sc)  - specify source region for new SG")
    print("    --dest  (or -ds)  - specify destination region for new SG")

if __name__ == "__main__":
    main()
