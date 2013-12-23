#include <stdio.h>
#include <stdlib.h>

typedef unsigned char uint8;

typedef struct {
    uint8 *data;
    int size;
    int maxsize;
} r_array;

uint8 readbyte(FILE *f,int *eof_flag);
int check_eof(int eof_flag);
r_array *init_r_array();
void insert_byte_r_array(r_array *r,uint8 b);
uint8 get_byte_r_array(r_array *r,int index);
void delete_r_array(r_array *r);
uint8 reverse_bits(uint8 b);


int main(int argc,char *argv[]){
    if(argc < 3){
        printf("Usage: dekirby <infilename> <outfilename> <offset>\n");
        printf("\tHexidecimal Offsets must be preceeded by 0x\n");
        printf("\tIf offset is ommited, it is assumed 0\n");
        return 0;
    }

    char *infilename, *outfilename;
    int offset=0;
    infilename = argv[1];
    outfilename = argv[2];

    if(argc >= 3)
        if(argv[3][0] == '0' && (argv[3][1] == 'x' || argv[3][1] == 'X'))
            sscanf(&argv[3][2],"%x",&offset);
        else offset=atoi(argv[3]);

    printf("%s %s %d\n",infilename,outfilename,offset);
    
    //open the input file pointer
    FILE *infp = fopen(infilename,"rb");
    if(offset != 0)fseek(infp,offset,SEEK_SET);

    //init the resiziable array for our output
    r_array *out_array = init_r_array();

    //define error flag
    int eof_flag=0;
    
    int i;

    int commands_used[7];
    for(i = 0;i < 7;i++)commands_used[i] = 0;
    int command_count=0;

    while(1){
        uint8 header_byte;
        header_byte = readbyte(infp,&eof_flag);
        if(check_eof(eof_flag))goto error;

        if(header_byte == 0xff)break;
        int command, length;

        command = header_byte >> 5;
        length = header_byte&0x1f + 1;

        //if this is an extended command (type 111)
        if(command == 7){
            //get command and length from 2 byte header
            uint8 header_byte2 = readbyte(infp,&eof_flag);
            if(check_eof(eof_flag))goto error;
            command = (header_byte>>2)&0x07;
            length = header_byte2 + header_byte&0x03 + 1;
        }

        uint8 b;
        uint8 b2;
        int addr;
        switch(command){
            case 0:
                //copy input to output
                for(i = 0;i < length;i++){
                    b = readbyte(infp,&eof_flag);
                    if(check_eof(eof_flag))goto error;
                    insert_byte_r_array(out_array,b);
                }
                commands_used[0]++;
                break;
            case 1:
                b = readbyte(infp,&eof_flag);
                if(check_eof(eof_flag))goto error;
                for(i = 0;i < length;i++){
                    insert_byte_r_array(out_array,b);
                }
                commands_used[1]++;
                break;
            case 2:
                b = readbyte(infp,&eof_flag);
                if(check_eof(eof_flag))goto error;
                b2 = readbyte(infp,&eof_flag);
                if(check_eof(eof_flag))goto error;
                for(i = 0;i < length;i++){
                    insert_byte_r_array(out_array,b);
                    insert_byte_r_array(out_array,b2);
                }
                commands_used[2]++;
                break;
            case 3:
                b = readbyte(infp,&eof_flag);
                if(check_eof(eof_flag))goto error;
                for(i = 0;i < length;i++){
                    insert_byte_r_array(out_array,b);
                    b++;
                }
                commands_used[3]++;
                break;

            //parasyte's doc reports that 4 and 7(invalid type) do the same thing
            case 4:
            case 7:
                //data contains an address to copy data from
                addr = readbyte(infp,&eof_flag) << 8;
                if(check_eof(eof_flag))goto error;
                addr = readbyte(infp,&eof_flag);
                if(check_eof(eof_flag))goto error;
                for(i = 0;i < length;i++){
                    b = get_byte_r_array(out_array,addr++);
                    insert_byte_r_array(out_array,b);
                }
                commands_used[4]++;
                break;

            case 5:
                addr = readbyte(infp,&eof_flag) << 8;
                if(check_eof(eof_flag))goto error;
                addr = readbyte(infp,&eof_flag);
                if(check_eof(eof_flag))goto error;
                for(i = 0;i < length;i++){
                    b = get_byte_r_array(out_array,addr++);
                    insert_byte_r_array(out_array,reverse_bits(b));
                }
                commands_used[5]++;
                break;
            case 6:
                addr = readbyte(infp,&eof_flag) << 8;
                if(check_eof(eof_flag))goto error;
                addr = readbyte(infp,&eof_flag);
                if(check_eof(eof_flag))goto error;
                for(i = 0;i < length;i++){
                    b = get_byte_r_array(out_array,addr--);
                    if(addr < 0){
                        printf("Decompression Error: Compression type 6 moved below beginning of input stream\n");
                        goto error;
                    }
                    insert_byte_r_array(out_array,b);
                }
                commands_used[6]++;
                break;
        }
        command_count++;
    }
    //can't break out of multiple blocks in C
    error:;

    //get the compressed size
    int compressed_size=ftell(infp) - offset;

    int decompressed_size = out_array->size;

    //close the input file
    fclose(infp);

    //write data to file
    FILE *outfp = fopen(outfilename,"wb");
    for(i = 0;i < out_array->size;i++){
        fputc(out_array->data[i],outfp);
    }
    fclose(outfp);

    //delete the resizable array
    delete_r_array(out_array);

    printf("Decompression Finished... some stats: \n");
    printf("-Compression Header Command Used Count-\n");
    printf("0\t1\t2\t3\t4\t5\t6\n");
    printf("%d\t%d\t%d\t%d\t%d\t%d\t%d\n",commands_used[0],commands_used[1],commands_used[2]
        ,commands_used[3],commands_used[4],commands_used[5],commands_used[6]);
    printf("total blocks: %d\n",command_count);
    printf("compressed size: %d\n",compressed_size);
    printf("decompressed size: %d\n",decompressed_size);
}

uint8 readbyte(FILE *f,int *eof_flag){
    int b = fgetc(f);
    if(b == EOF){
        *eof_flag = 1;
    }
    return (uint8)b;
}

//This error checking function ensures the end of file hasn't been
//reached before it is supposed to.
int check_eof(int eof_flag){
    //check eof flag... this indicates error
    if(eof_flag == 1){
        printf("Decompression Error: EOF Reached. Aborting.\n");
        return 1;
    }
    return 0;
}


//    -------Resizable Array Functions--------

//initiailize a new resizable array
r_array *init_r_array(){
    r_array *r = (r_array *)malloc(sizeof(r_array));
    r->size = 0;
    r->maxsize = 65536;
    r->data = (uint8 *)malloc(65536);
}

//insert a byte into resizable array
void insert_byte_r_array(r_array *r,uint8 b){
    r->data[r->size++] = b;
    if(r->size == r->maxsize){
        r->maxsize += 65536;
        r->data = (uint8 *)realloc((void *)r->data,r->maxsize);
    }
}

//get a byte from a resizable array
uint8 get_byte_r_array(r_array *r,int index){
    return r->data[index];
}

void delete_r_array(r_array *r){
    free(r->data);
    free(r);
}

uint8 reverse_bits(uint8 b){
    int i;
    uint8 r=0;
    for(i = 0;i < 8;i++){
        if(b&1)r|=0x80;
        b<<=1;
        r>>=1;
    }
}

